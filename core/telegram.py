"""
Recordatorios de estudio por Telegram (opt-in).

Como funciona:
  * La persona pulsa "Conectar Telegram" en la seccion Plan: la app crea un codigo de un solo uso
    (vence a los 10 minutos, se guarda solo su hash) y un enlace t.me/<bot>?start=<codigo>.
  * Al abrir el enlace, Telegram le manda al bot "/start <codigo>"; el bot lo canjea y guarda el chat.
    Nunca se pide el telefono ni se lee nada mas del chat: solo se responde a /start, /stop y /ayuda.
  * Un trabajador en segundo plano mira el plan de cada persona vinculada y, a la hora de un bloque de
    estudio (unos minutos antes), le manda un aviso corto. Cada aviso se manda una sola vez.
  * Con /stop (o desde la app) se borra el vinculo en cualquier momento.

Privacidad: el aviso lleva la hora, los minutos y el nombre de la tarea o evaluacion; Telegram no cifra
de extremo a extremo los mensajes de los bots. El token del bot (TELEGRAM_BOT_TOKEN) va en el .env y
NUNCA se imprime: las direcciones de la API lo contienen, por eso los errores no incluyen la URL.
"""
import hashlib
import secrets
from datetime import timedelta

import requests

from core import planificador
from core.tiempo import ahora_iso, iso_a_datetime

API = "https://api.telegram.org"
CODIGO_VIGENCIA_MIN = 10
AVISOS_MIN = (5, 10, 15, 30)
TOLERANCIA_TARDE_MIN = 10          # si el trabajador se atraso, aun avisa hasta 10 min despues de la hora
MAX_ITEM = 60

_estado = {"usuario_bot": None}


class TelegramError(Exception):
    def __init__(self, mensaje, codigo=None):
        super().__init__(mensaje)
        self.codigo = codigo


# ------------------------------------------------------------------ API de Telegram
def _llamar(token: str, metodo: str, datos: dict = None, timeout: int = 15):
    try:
        r = requests.post(f"{API}/bot{token}/{metodo}", json=datos or {}, timeout=timeout)
    except requests.RequestException:
        raise TelegramError("error de red con Telegram") from None      # sin URL: lleva el token
    try:
        j = r.json()
    except ValueError:
        raise TelegramError(f"respuesta no válida de Telegram ({r.status_code})", r.status_code) from None
    if not j.get("ok"):
        raise TelegramError(f"Telegram rechazó la petición ({j.get('error_code', r.status_code)})", j.get("error_code"))
    return j["result"]


def enviar(token: str, chat_id, texto: str):
    return _llamar(token, "sendMessage", {"chat_id": chat_id, "text": texto, "disable_web_page_preview": True})


def actualizaciones(token: str, offset=None, espera: int = 8) -> list:
    datos = {"timeout": espera, "allowed_updates": ["message"]}
    if offset is not None:
        datos["offset"] = offset
    return _llamar(token, "getUpdates", datos, timeout=espera + 10)


def preparar_bot(token: str) -> str:
    """Quita un webhook previo (si lo hay, getUpdates no funciona) y guarda el usuario del bot."""
    _llamar(token, "deleteWebhook", {})
    _estado["usuario_bot"] = _llamar(token, "getMe").get("username")
    return _estado["usuario_bot"]


def usuario_bot(entorno_usuario: str = None, token: str = None):
    """Usuario del bot: el que ya se conoce, el del .env o, si hay token, se le pregunta a Telegram (getMe).
    Lanza TelegramError si Telegram rechaza el token o no hay red."""
    if not _estado["usuario_bot"] and not entorno_usuario and token:
        _estado["usuario_bot"] = _llamar(token, "getMe").get("username")
    return _estado["usuario_bot"] or (entorno_usuario or "").lstrip("@") or None


# ------------------------------------------------------------------ vinculo
def _hash(codigo: str) -> str:
    return hashlib.sha256(codigo.encode("utf-8")).hexdigest()


def crear_codigo(conn, est: int, ahora: str = None) -> str:
    ahora = ahora or ahora_iso("seconds")
    codigo = secrets.token_urlsafe(9)
    vence = (iso_a_datetime(ahora) + timedelta(minutes=CODIGO_VIGENCIA_MIN)).replace(tzinfo=None).isoformat(timespec="seconds")
    with conn:
        conn.execute("""
            INSERT INTO telegram_vinculo (estudiante_id, codigo_hash, codigo_vence) VALUES (?, ?, ?)
            ON CONFLICT(estudiante_id) DO UPDATE SET codigo_hash = excluded.codigo_hash, codigo_vence = excluded.codigo_vence""",
            (est, _hash(codigo), vence))
    return codigo


def canjear_codigo(conn, codigo: str, chat_id, ahora: str = None):
    """Vincula el chat con la persona dueña del codigo. Devuelve su id o None si el codigo no sirve."""
    ahora = ahora or ahora_iso("seconds")
    codigo = (codigo or "").strip()
    if not codigo or len(codigo) > 64:
        return None
    fila = conn.execute("SELECT estudiante_id FROM telegram_vinculo WHERE codigo_hash = ? AND codigo_vence >= ?",
                        (_hash(codigo), ahora)).fetchone()
    if not fila:
        return None
    est = fila["estudiante_id"]
    with conn:
        # un chat pertenece a una sola persona: si estaba en otra cuenta, se le quita
        conn.execute("DELETE FROM telegram_vinculo WHERE chat_id = ? AND estudiante_id != ?", (str(chat_id), est))
        conn.execute("""UPDATE telegram_vinculo SET chat_id = ?, codigo_hash = NULL, codigo_vence = NULL,
                        activo = 1, vinculado_en = ? WHERE estudiante_id = ?""", (str(chat_id), ahora, est))
    return est


def estado(conn, est: int) -> dict:
    f = conn.execute("SELECT chat_id, avisar_min, activo, vinculado_en FROM telegram_vinculo WHERE estudiante_id = ?", (est,)).fetchone()
    return {"vinculado": bool(f and f["chat_id"]), "activo": bool(f and f["chat_id"] and f["activo"]),
            "avisar_min": f["avisar_min"] if f else 10, "vinculado_en": f["vinculado_en"] if f else None}


def guardar_preferencias(conn, est: int, activo=None, avisar_min=None) -> None:
    if avisar_min is not None and (isinstance(avisar_min, bool) or avisar_min not in AVISOS_MIN):
        raise ValueError(f"Avisar: elige entre {', '.join(map(str, AVISOS_MIN))} minutos antes.")
    if activo is not None and not isinstance(activo, bool):
        raise ValueError("Activo: indica sí o no.")
    if not estado(conn, est)["vinculado"]:
        raise ValueError("Primero conecta tu Telegram.")
    with conn:
        if avisar_min is not None:
            conn.execute("UPDATE telegram_vinculo SET avisar_min = ? WHERE estudiante_id = ?", (avisar_min, est))
        if activo is not None:
            conn.execute("UPDATE telegram_vinculo SET activo = ? WHERE estudiante_id = ?", (1 if activo else 0, est))


def desvincular(conn, est: int) -> None:
    with conn:
        conn.execute("DELETE FROM telegram_vinculo WHERE estudiante_id = ?", (est,))


def desvincular_chat(conn, chat_id) -> int:
    with conn:
        return conn.execute("DELETE FROM telegram_vinculo WHERE chat_id = ?", (str(chat_id),)).rowcount


# ------------------------------------------------------------------ mensajes que recibe el bot
AYUDA = ("Soy el bot de recordatorios de AcademIA (proyecto académico, no oficial de la UNEMI).\n"
         "Para conectarte, abre AcademIA > Plan > «Conectar Telegram» y pulsa el enlace que aparece.\n"
         "Comandos: /stop deja de enviarte avisos y borra el vínculo · /ayuda muestra este mensaje.")


def responder(conn, mensaje: dict, ahora: str = None):
    """Procesa un mensaje del bot. Devuelve (chat_id, texto) a responder, o None si se ignora."""
    chat = (mensaje or {}).get("chat") or {}
    if chat.get("type") != "private" or "id" not in chat:
        return None                                   # solo chats privados, nunca grupos
    texto = ((mensaje or {}).get("text") or "").strip()
    partes = texto.split()
    orden = partes[0].split("@")[0].lower() if partes else ""
    if orden == "/start" and len(partes) > 1:
        est = canjear_codigo(conn, partes[1], chat["id"], ahora)
        if est is None:
            return chat["id"], "Ese código no sirve o ya venció. Genera uno nuevo en AcademIA > Plan > «Conectar Telegram»."
        return chat["id"], ("Listo, quedaste conectado. Te avisaré unos minutos antes de cada bloque de tu plan de estudio. "
                            "Puedes cambiar cuánto antes o pausar los avisos en AcademIA > Plan. /stop para dejar de recibirlos.")
    if orden == "/stop":
        n = desvincular_chat(conn, chat["id"])
        return chat["id"], "Listo, borré el vínculo y no recibirás más avisos." if n else "No tenías avisos activos."
    return chat["id"], AYUDA


# ------------------------------------------------------------------ recordatorios
def _texto_aviso(bloque: dict) -> str:
    tipo = {"tarea": "Tarea", "evaluacion": "Evaluación", "refuerzo": "Refuerzo"}.get(bloque["tipo"], "Estudio")
    item = (bloque["item"] or "")[:MAX_ITEM]
    return (f"⏰ AcademIA: a las {bloque['inicio']} te toca estudiar {bloque['minutos']} min.\n"
            f"{tipo}: {item}\n"
            "Abre AcademIA para ver por qué y cómo estudiarlo. /stop para dejar de recibir avisos.")


def pendientes(conn, ahora: str = None, generar=planificador.generar) -> list:
    """Avisos que toca mandar ahora: [(estudiante_id, chat_id, texto, clave)]. No manda nada dos veces."""
    ahora = ahora or ahora_iso("seconds")
    ahora_dt = iso_a_datetime(ahora).replace(tzinfo=None)
    salida = []
    # El plan se arma "desde las 00:00": asi las horas del dia no se corren a medida que pasa el tiempo
    # (el plan normal recorta lo que ya paso) y un bloque conserva siempre la misma hora y clave.
    inicio_dia = ahora_dt.replace(hour=0, minute=0, second=0, microsecond=0).isoformat(timespec="seconds")
    for v in conn.execute("SELECT estudiante_id, chat_id, avisar_min FROM telegram_vinculo WHERE chat_id IS NOT NULL AND activo = 1").fetchall():
        try:
            hoy = generar(conn, v["estudiante_id"], ahora=inicio_dia)["dias"][0]   # plan completo: lo que vence luego tambien cuenta hoy
        except Exception as e:
            print(f"[telegram] No se pudo leer el plan de un estudiante: {type(e).__name__}")
            continue
        for b in hoy["bloques"]:
            if not b.get("inicio"):
                continue
            h, m = map(int, b["inicio"].split(":"))
            inicio = ahora_dt.replace(hour=h, minute=m, second=0, microsecond=0)
            if not (inicio - timedelta(minutes=v["avisar_min"]) <= ahora_dt <= inicio + timedelta(minutes=TOLERANCIA_TARDE_MIN)):
                continue
            clave = f"{hoy['fecha']}|{b['inicio']}|{b['clave']}"
            if conn.execute("SELECT 1 FROM recordatorio_enviado WHERE estudiante_id = ? AND clave = ?",
                            (v["estudiante_id"], clave)).fetchone():
                continue
            salida.append((v["estudiante_id"], v["chat_id"], _texto_aviso(b), clave))
    return salida


def enviar_pendientes(conn, token: str, ahora: str = None, enviar_fn=enviar, generar=planificador.generar) -> int:
    """Manda los avisos que tocan y los marca como enviados. Devuelve cuantos salieron."""
    ahora = ahora or ahora_iso("seconds")
    n = 0
    for est, chat_id, texto, clave in pendientes(conn, ahora, generar):
        try:
            enviar_fn(token, chat_id, texto)
        except TelegramError as e:
            print(f"[telegram] No se pudo enviar un aviso: {e}")
            if e.codigo in (400, 403):                  # bot bloqueado o chat borrado: no insistir
                desvincular_chat(conn, chat_id)
            continue
        with conn:
            conn.execute("INSERT OR IGNORE INTO recordatorio_enviado (estudiante_id, clave, enviado_en) VALUES (?, ?, ?)",
                         (est, clave, ahora))
        n += 1
    corte = (iso_a_datetime(ahora) - timedelta(days=3)).replace(tzinfo=None).isoformat(timespec="seconds")
    with conn:
        conn.execute("DELETE FROM recordatorio_enviado WHERE enviado_en < ?", (corte,))
    return n
