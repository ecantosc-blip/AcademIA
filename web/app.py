"""
Interfaz web del asistente - version multi-usuario.

Cada persona inicia sesion con sus propios tokens de sesion (token de
Moodle + token del SGA); la app NUNCA recibe ni guarda contraseñas
(autenticacion delegada por token). El sistema identifica a cada estudiante por
su userid real de Moodle (unico por persona), en vez del estudiante_id=1
fijo que se usaba en el prototipo inicial de un solo usuario.

Notas de diseño pensando en una eventual adopcion institucional:
- Las sesiones se guardan EN EL SERVIDOR (diccionario en memoria, con un
  id de sesion aleatorio en una cookie httponly) en vez de en una cookie
  firmada del lado del cliente - esto evita exponer tokens/contraseñas
  dentro de la cookie del navegador.
- LIMITACION conocida de este prototipo: las sesiones viven en memoria del
  proceso Python, por lo que se pierden si el servidor se reinicia. Para
  produccion real, esto deberia guardarse en Redis o una tabla de base de
  datos con expiracion.
"""
import os
import sys
import time
from contextlib import nullcontext
from urllib.parse import urlparse

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask, request, jsonify, render_template, redirect, make_response, Response
import requests
from dotenv import load_dotenv

load_dotenv()

from moodle.client import MoodleClient, extraer_token_moodle
from sga.client import SGAClient, segundos_para_vencer, decodificar_jwt, PerfilSinRefreshError
from core.assistant import AssistantLLM
from core.sync_all import sync as sync_estudiante
from db.setup import get_connection, init_db
from core.privacidad import depurar
from core.tiempo import ahora_iso
from core import foto, carnet_pdf

# Crea las tablas (y aplica migraciones) si la base no existe todavia.
# Es idempotente: si ya existen, no toca los datos.
init_db()
import sessions_store
import panel
import mis_datos
import limites
from core.estudio import Estudio

app = Flask(__name__)

# Detras de un proxy (Render, Railway, Cloudflare, nginx...) la IP real del
# cliente llega en cabeceras X-Forwarded-*. SOLO se confia en ellas si se
# declara TRUST_PROXY=1; si no, cualquiera podria falsearlas y saltarse los limites.
if os.getenv("TRUST_PROXY") == "1":
    from werkzeug.middleware.proxy_fix import ProxyFix
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

# Las sesiones ya NO viven en un diccionario en memoria: se guardan en SQLite
# (cifradas y con expiracion) en sessions_store.py, asi sobreviven a un
# reinicio del servidor. Lo unico que sigue en memoria es el historial de
# conversacion de cada asistente (ASSISTANTS), que se reinicia vacio.
MAX_SESIONES_MEMORIA = int(os.getenv("MAX_SESIONES_MEMORIA", "2000"))
ASSISTANTS = {}
ESTUDIOS = {}   # espacio de estudio por sesion (practica en curso e historial del tutor)
COOKIE_NAME = "sid"
COOKIE_SECURE = os.getenv("COOKIE_SECURE") == "1"  # poner COOKIE_SECURE=1 si se sirve por HTTPS


def _guardar_acotado(dic: dict, clave: str, valor):
    """Guarda en un dict en memoria sin dejar que crezca sin limite (descarta lo mas antiguo)."""
    dic[clave] = valor
    while len(dic) > MAX_SESIONES_MEMORIA:
        viejo = dic.pop(next(iter(dic)))
        cerrar = getattr(viejo, "close", None)
        if cerrar:
            cerrar()


def _ip() -> str:
    return request.remote_addr or "desconocida"


def _demasiadas(espera: int, api: bool = True):
    msg = f"Vas muy rápido. Intenta de nuevo en {espera} s."
    resp = jsonify({"error": msg}) if api else Response(msg, status=429)
    resp.status_code = 429
    resp.headers["Retry-After"] = str(espera)
    return resp


def _es_api() -> bool:
    return request.path == "/sync" or request.path.startswith("/api/")


def _origen_seguro():
    """Rechaza POST iniciados desde otros sitios sin cambiar los formularios existentes.

    Los navegadores modernos envian Origin y/o Sec-Fetch-Site. Si ambos faltan,
    Referer permite comprobar el origen cuando esta disponible. No se confia en
    X-Forwarded-* salvo cuando ProxyFix esta expresamente habilitado.
    """
    if request.method not in ("POST", "PUT", "PATCH", "DELETE"):
        return True
    if request.headers.get("Sec-Fetch-Site", "").lower() == "cross-site":
        return False
    origen = request.headers.get("Origin")
    if origen == "null":
        return False
    referencia = origen or request.headers.get("Referer")
    if referencia:
        partes = urlparse(referencia)
        return (partes.scheme in ("http", "https")
                and partes.scheme == request.scheme
                and partes.netloc.lower() == request.host.lower())
    return True


@app.before_request
def _proteger_origen():
    if not _origen_seguro():
        if _es_api() or request.is_json:
            return jsonify({"error": "Solicitud rechazada: origen no permitido"}), 403
        return Response("Solicitud rechazada: origen no permitido", status=403)


@app.before_request
def _limite_general():
    ok, espera = limites.revisar("general", _ip())
    if not ok:
        return _demasiadas(espera, _es_api())


@app.after_request
def _cabeceras_seguridad(resp):
    """Cabeceras que endurecen el navegador del usuario (sin romper las paginas actuales)."""
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    resp.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    resp.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; "
        "font-src 'self'; img-src 'self' data:; connect-src 'self'; "
        "frame-ancestors 'none'; base-uri 'self'; form-action 'self'; object-src 'none'")
    if request.path.startswith("/api/") or request.path in ("/", "/estudio", "/sync", "/login"):
        resp.headers["Cache-Control"] = "no-store"     # datos personales: nunca en cache
    if request.is_secure:
        resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
    return resp


def get_session():
    sid = request.cookies.get(COOKIE_NAME)
    sesion = sessions_store.obtener(sid)
    if not sesion:
        # Sesion inexistente o vencida: se libera tambien su asistente en memoria.
        if sid:
            asistente = ASSISTANTS.pop(sid, None)
            if asistente:
                asistente.close()
            ESTUDIOS.pop(sid, None)
        return None, None
    return sid, sesion


def avisos_de_sincronizacion(estudiante_id: int, desde_iso: str) -> list:
    """
    Lee el registro de la sincronizacion que acaba de correr y devuelve, en
    texto claro, los fallos que hubo. Antes estos errores solo salian en la
    consola del servidor: el estudiante entraba "normal" y el asistente
    respondia que no tenia datos, sin explicar por que.
    """
    conn = get_connection()
    try:
        filas = conn.execute(
            "SELECT plataforma, detalle FROM sesion_extraccion "
            "WHERE estudiante_id = ? AND estado = 'fallida' AND fecha >= ? ORDER BY id",
            (estudiante_id, desde_iso),
        ).fetchall()
    finally:
        conn.close()

    avisos = []
    for fila in filas:
        detalle = fila["detalle"] or ""
        if fila["plataforma"] == "sga" and "Token expirado" in detalle:
            avisos.append(
                "El token del ERP venció: no pude traer tus notas oficiales, horario ni registro "
                "académico. Cierra sesión y entra de nuevo con un token nuevo del ERP."
            )
        elif fila["plataforma"] == "moodle":
            avisos.append(f"No pude sincronizar Moodle: {detalle}")
        else:
            avisos.append(f"No pude sincronizar el ERP: {detalle}")
    return avisos


ESPERA_REINTENTO_RENOVACION = 300   # segundos entre intentos de renovar si falla


def renovar_token_erp(sid: str, sesion: dict) -> bool:
    """
    Si al token del ERP le quedan menos de 5 min y hay token de renovacion, lo
    renueva y guarda el nuevo en la sesion. Si falla, no insiste hasta pasados 5 min.
    """
    acceso, refresco = sesion.get("sga_access_token"), sesion.get("sga_refresh_token")
    if not acceso or not refresco:
        return False
    resto = segundos_para_vencer(acceso)
    if resto is None or resto > 300:
        return False
    if time.time() - sesion.get("sga_renov_intento", 0) < ESPERA_REINTENTO_RENOVACION:
        return False
    cliente = SGAClient(os.getenv("SGA_BASE_URL", "https://sga.unemi.edu.ec"), acceso, refresco)
    ok = cliente.refresh_access_token()
    sesion["sga_renov_intento"] = time.time()
    if ok:
        sesion["sga_access_token"] = cliente.access_token
        sesion["sga_refresh_token"] = cliente.refresh_token
    sessions_store.guardar(sid, sesion)
    return ok


def get_asistente_para(sid: str, estudiante_id: int) -> AssistantLLM:
    if sid not in ASSISTANTS:
        _guardar_acotado(ASSISTANTS, sid, AssistantLLM(estudiante_id=estudiante_id))
    return ASSISTANTS[sid]



@app.route("/api/foto")
def api_foto():
    """Foto de perfil del propio estudiante, pedida a Moodle desde el servidor.

    El token de Moodle nunca llega al navegador. La foto no se guarda en la base de datos:
    solo se reenvía, y como todo /api/ sale sin caché.
    """
    sid, sesion = get_session()
    if not sesion:
        return jsonify({"error": "Sesión no válida."}), 401
    datos = tipo = None
    motivo = "sin foto"
    if sesion.get("foto_erp_url"):     # la foto oficial del ERP tiene prioridad
        datos, tipo, motivo = foto.descargar_erp(sesion["foto_erp_url"], sesion.get("sga_access_token", ""),
                                                 os.getenv("SGA_BASE_URL", "https://sga.unemi.edu.ec"))
    if datos is None and sesion.get("foto_url"):
        datos, tipo, motivo = foto.descargar(sesion["foto_url"], sesion["moodle_token"])
    if datos is None:
        if sesion.get("foto_erp_url") or sesion.get("foto_url"):
            print(f"[foto] No se mostró la foto de perfil: {motivo}")
        return ("", 404)
    return Response(datos, mimetype=tipo)   # /api/* ya sale con Cache-Control: no-store


@app.route("/")
def index():
    sid, sesion = get_session()
    if not sesion:
        return redirect("/login")
    # Aviso de la sincronizacion del login (si hubo fallos): se muestra una
    # sola vez y se borra de la sesion.
    aviso = sesion.pop("aviso_sync", None)
    if aviso:
        sessions_store.guardar(sid, sesion)
    return render_template("index.html", nombre=sesion.get("nombre", ""), aviso=aviso,
                           tiene_foto=bool(sesion.get("foto_url") or sesion.get("foto_erp_url")))


@app.route("/login", methods=["GET"])
def login_form():
    return render_template(
        "login.html",
        error=None,
        moodle_base_url=os.getenv("MOODLE_BASE_URL", "https://aulagradoa.unemi.edu.ec"),
    )


@app.route("/login", methods=["POST"])
def login_submit():
    ok, espera = limites.revisar("login", _ip())
    if not ok:
        return render_template(
            "login.html",
            error=f"Demasiados intentos. Espera {max(1, espera // 60)} min y vuelve a intentarlo.",
            moodle_base_url=os.getenv("MOODLE_BASE_URL", "https://aulagradoa.unemi.edu.ec"),
        ), 429
    # Dos formas de entrar (ISO/IEC 27001: la contrasena se usa UNA vez y no se guarda):
    #  1. Con usuario y contrasena de Moodle (lo normal): el SERVIDOR le pide a Moodle un token
    #     (login/token.php, el mismo mecanismo de la app oficial de Moodle), descarta la contrasena
    #     y trabaja solo con el token, que vence y es de solo lectura.
    #  2. Avanzado: pegar el token de Moodle ya generado (sin contrasena).
    # El ERP es OPCIONAL: su login tiene deteccion de bots y no se automatiza; si el estudiante
    # pega su token, se agrega horario de clases, foto y notas oficiales.
    moodle_base_url = os.getenv("MOODLE_BASE_URL", "https://aulagradoa.unemi.edu.ec")
    usuario = (request.form.get("moodle_usuario") or "").strip()
    clave = request.form.get("moodle_clave") or ""
    moodle_token = extraer_token_moodle(request.form.get("moodle_token") or "")
    sga_access_token = (request.form.get("sga_access_token") or "").strip()
    sga_refresh_token = (request.form.get("sga_refresh_token") or "").strip()
    sga_alumno_id = (request.form.get("sga_alumno_id") or "").strip()

    def _error_login(texto, codigo=200):
        return render_template("login.html", error=texto, moodle_base_url=moodle_base_url), codigo

    if not moodle_token:
        if not usuario or not clave:
            return _error_login("Escribe tu usuario y contraseña de Moodle.")
        # Freno por usuario: esta pagina no puede servir para adivinar contrasenas ajenas.
        ok_u, espera_u = limites.revisar("login_usuario", usuario.lower())
        if not ok_u:
            return _error_login(f"Demasiados intentos con ese usuario. Espera {max(1, espera_u // 60)} min.", 429)
        try:
            with limites.cupo_sync.tomar(20):
                moodle_token = MoodleClient(base_url=moodle_base_url).login(usuario, clave)
        except limites.Ocupado:
            return _error_login("Hay mucha gente conectándose ahora mismo. Inténtalo de nuevo en un momento.", 503)
        except ValueError:      # Moodle rechazo las credenciales; no se repite lo que escribio
            return _error_login("Usuario o contraseña incorrectos.")
        except RuntimeError:
            return _error_login("No pude conectar con Moodle. Inténtalo de nuevo en unos minutos.")
        finally:
            clave = None        # la contrasena no vuelve a usarse ni se guarda en ningun sitio

    tiene_erp = bool(sga_access_token)

    # El token del ERP (si lo hay) trae su vencimiento dentro: se revisa sin llamar a la red.
    resto = segundos_para_vencer(sga_access_token) if tiene_erp else None
    if tiene_erp and resto is None and sga_access_token.count(".") != 2:
        return _error_login("El token del ERP no parece válido: debe ser el valor largo de \"access\" "
                            "(empieza con eyJ...). Cópialo de nuevo desde login.json, o déjalo vacío.")
    if tiene_erp and resto is not None and resto <= 0 and sga_refresh_token:
        intento = SGAClient(os.getenv("SGA_BASE_URL", "https://sga.unemi.edu.ec"), sga_access_token, sga_refresh_token)
        if intento.refresh_access_token():
            sga_access_token, sga_refresh_token = intento.access_token, intento.refresh_token
            resto = segundos_para_vencer(sga_access_token)
    if tiene_erp and resto is not None and resto <= 0:
        minutos = int(-resto // 60)
        hace = f"hace {minutos} min" if minutos < 120 else f"hace {minutos // 60} h"
        return _error_login(f"El token del ERP ya venció ({hace}). Copia uno nuevo, o déjalo vacío para entrar solo con Moodle.")

    # La parte lenta (hablar con Moodle y el ERP y sincronizar) entra por un cupo:
    # si hay muchos ingresando a la vez, se espera un poco y luego se pide reintentar.
    try:
        with limites.cupo_sync.tomar(20):
            moodle = MoodleClient(base_url=moodle_base_url, token=moodle_token)
            try:
                # Valida el token: si es incorrecto o ya venció, Moodle responde con error.
                info = moodle.get_site_info()
            except Exception as e:
                return _error_login(f"El token de Moodle no es válido o ya venció. Genera uno nuevo y vuelve a intentarlo. ({depurar(e)})")

            estudiante_id = info["userid"]
            nombre = info.get("fullname", "Estudiante")

            sga = SGAClient(
                base_url=os.getenv("SGA_BASE_URL", "https://sga.unemi.edu.ec"),
                access_token=sga_access_token,
                refresh_token=sga_refresh_token,
            )
            sga_alumno_id_final = ""

            aviso_perfil = ""
            if tiene_erp:
                # Si la cuenta tiene mas de un perfil de carrera (ej. Nivelacion + Grado), se cambia
                # al perfil regular ("Grado"), que es el que tiene horario/notas/registro completos.
                try:
                    if sga.cambiar_a_perfil_regular():
                        print("[login] Varios perfiles de carrera: cambiado al de tipo 'Grado'.")
                except PerfilSinRefreshError:
                    aviso_perfil = ("Tu cuenta del ERP tiene otra carrera además de la que está activa, pero "
                                    "para cambiarme a ella automáticamente necesito también tu token de "
                                    "renovación (\"refresh\"). Agrégalo (es el campo opcional, debajo del "
                                    "token del ERP) y vuelve a entrar para ver el horario y las notas de esa carrera.")
                    print("[login] Aviso: hay otra carrera de Grado pero falta el refresh para cambiarse.")
                except Exception as e:
                    print(f"[login] Aviso: no se pudo verificar/cambiar el perfil de carrera: {depurar(e)}")
                # El ID de alumno se extrae solo; el campo del formulario permite forzarlo a mano.
                sga_alumno_id_final = sga_alumno_id or sga.get_alumno_id_automatico() or ""

            # Sincronizacion inicial con los datos de ESTA persona especificamente.
            inicio_sync = ahora_iso()
            try:
                sync_estudiante(
                    estudiante_id=estudiante_id,
                    moodle_client=moodle,
                    sga_client=sga,
                    sga_alumno_id=sga_alumno_id_final or None,
                )
            except Exception as e:
                print(f"[login] Aviso: la sincronizacion inicial tuvo problemas: {depurar(e)}")

            avisos = avisos_de_sincronizacion(estudiante_id, inicio_sync)
            if aviso_perfil:
                avisos.append(aviso_perfil)
            if not tiene_erp:
                avisos.append("Entraste solo con Moodle: no tendrás horario de clases ni foto del ERP. "
                              "Puedes conectar el ERP al volver a entrar (opcional).")

    except limites.Ocupado:
        return _error_login("Hay mucha gente conectándose ahora mismo. Inténtalo de nuevo en un momento.", 503)

    foto_erp_url = ""
    if tiene_erp:
        try:
            foto_erp_url = foto.buscar_en_sga(decodificar_jwt(sga.access_token),
                                              os.getenv("SGA_BASE_URL", "https://sga.unemi.edu.ec"))
        except Exception:
            foto_erp_url = ""

    sid = sessions_store.crear({
        "aviso_sync": " ".join(avisos),
        "estudiante_id": estudiante_id,
        "nombre": nombre,
        "foto_url": foto.url_valida(info.get("userpictureurl"), moodle_base_url),
        "foto_erp_url": foto_erp_url,
        "moodle_token": moodle_token,            # solo el token (vence); la contrasena nunca se guarda
        "sga_access_token": sga.access_token,   # ya actualizado si hubo cambio de perfil
        "sga_refresh_token": sga.refresh_token,  # idem
        "sga_alumno_id": sga_alumno_id_final,
    })

    resp = make_response(redirect("/"))
    resp.set_cookie(
        COOKIE_NAME, sid,
        httponly=True, samesite="Lax",
        max_age=sessions_store.TTL_SEGUNDOS,   # la cookie vence junto con la sesion
        secure=(COOKIE_SECURE or request.is_secure),
    )
    return resp


@app.route("/logout", methods=["POST"])
def logout():
    sid, _ = get_session()
    if sid:
        sessions_store.eliminar(sid)
        ESTUDIOS.pop(sid, None)
        asistente = ASSISTANTS.pop(sid, None)
        if asistente:
            asistente.close()
    resp = make_response(redirect("/login"))
    resp.delete_cookie(COOKIE_NAME)
    return resp


@app.route("/sync", methods=["POST"])
def resync():
    sid, sesion = get_session()
    if not sesion:
        return jsonify({"error": "No has iniciado sesión"}), 401
    ok, espera = limites.revisar("sync", sid)
    if not ok:
        return _demasiadas(espera)
    try:
        with limites.cupo_sync.tomar(5):
            return _resync(sid, sesion)
    except limites.Ocupado:
        return jsonify({"error": "Hay muchas actualizaciones en curso. Inténtalo de nuevo en un momento."}), 503


def _resync(sid: str, sesion: dict):
    moodle = MoodleClient(
        base_url=os.getenv("MOODLE_BASE_URL", "https://aulagradoa.unemi.edu.ec"),
        token=sesion["moodle_token"],
    )
    sga = SGAClient(
        base_url=os.getenv("SGA_BASE_URL", "https://sga.unemi.edu.ec"),
        access_token=sesion["sga_access_token"],
        refresh_token=sesion["sga_refresh_token"],
    )
    inicio_sync = ahora_iso()
    try:
        try:
            sga.cambiar_a_perfil_regular()
        except Exception:
            pass
        # Se guarda el token actualizado por si hubo cambio de perfil
        sesion["sga_access_token"] = sga.access_token
        sesion["sga_refresh_token"] = sga.refresh_token
        sessions_store.guardar(sid, sesion)

        try:
            alumno_id = sesion.get("sga_alumno_id") or sga.get_alumno_id_automatico()
        except Exception:
            alumno_id = None  # token del ERP vencido: el sync lo reportara como aviso
        try:
            sync_estudiante(
                estudiante_id=sesion["estudiante_id"],
                moodle_client=moodle,
                sga_client=sga,
                sga_alumno_id=alumno_id or None,
            )
        finally:
            # Si el token se renovo durante la lectura, la sesion guarda el nuevo.
            sesion["sga_access_token"] = sga.access_token
            sesion["sga_refresh_token"] = sga.refresh_token
            sessions_store.guardar(sid, sesion)
        # "avisos" lista lo que fallo (ej. token del ERP vencido); vacio = todo bien.
        return jsonify({"ok": True, "avisos": avisos_de_sincronizacion(sesion["estudiante_id"], inicio_sync)})
    except Exception as e:
        return jsonify({"error": depurar(e)}), 500


@app.errorhandler(Exception)
def _error_json(e):
    """Las rutas de la API siempre responden JSON, nunca una pagina HTML de error."""
    from werkzeug.exceptions import HTTPException
    if request.path == "/sync" or request.path.startswith("/api/"):
        code = e.code if isinstance(e, HTTPException) else 500
        return jsonify({"error": f"Error interno: {depurar(e)}"}), code
    if isinstance(e, HTTPException):
        return e
    raise e


@app.route("/api/panel")
def api_panel():
    sid, sesion = get_session()
    if not sesion:
        return jsonify({"error": "No has iniciado sesión"}), 401
    renovar_token_erp(sid, sesion)
    return jsonify(panel.construir_panel(sesion))


@app.route("/api/novedades")
def api_novedades():
    """Cambios detectados entre lecturas exitosas: los no vistos y los ultimos ya vistos."""
    sid, sesion = get_session()
    if not sesion:
        return jsonify({"error": "No has iniciado sesión"}), 401
    from core import cambios
    conn = get_connection()
    try:
        est = sesion["estudiante_id"]
        return jsonify({"nuevas": cambios.novedades(conn, est, solo_nuevas=True),
                        "anteriores": [n for n in cambios.novedades(conn, est, solo_nuevas=False, limite=40)
                                       if n["visto"]][:20]})
    finally:
        conn.close()


@app.route("/api/novedades/vistas", methods=["POST"])
def api_novedades_vistas():
    sid, sesion = get_session()
    if not sesion:
        return jsonify({"error": "No has iniciado sesión"}), 401
    from core import cambios
    conn = get_connection()
    try:
        return jsonify({"marcadas": cambios.marcar_vistas(conn, sesion["estudiante_id"])})
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Plan de estudio y alertas (Fase 3)
# ---------------------------------------------------------------------------
def _con_estudiante(fn):
    """Ejecuta fn(conn, estudiante_id, cuerpo_json) para la sesion actual. ValueError -> 400."""
    sid, sesion = get_session()
    if not sesion:
        return jsonify({"error": "No has iniciado sesión"}), 401
    conn = get_connection()
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        cuerpo = request.get_json(silent=True)
        return jsonify(fn(conn, sesion["estudiante_id"], cuerpo if isinstance(cuerpo, dict) else {}))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    finally:
        conn.close()


def _vista_plan(conn, est, _cuerpo=None):
    from core import planificador, preferencias
    materias = [{"id": f["materia_id"], "nombre": f["nombre"]} for f in conn.execute("""
        SELECT mat.materia_id, m.nombre FROM matricula mat JOIN materia m ON m.id = mat.materia_id
        WHERE mat.estudiante_id = ? AND mat.estado = 'en_curso' ORDER BY m.nombre""", (est,))]
    dif = preferencias.dificultades(conn, est)
    for m in materias:
        m["dificultad"] = dif.get(m["id"])
    from core import agenda_personal, telegram
    return {"plan": planificador.generar(conn, est), "preferencias": preferencias.obtener(conn, est),
            "materias": materias, "dias": preferencias.DIAS,
            "agenda": dict(agenda_personal.listar(conn, est), clases=agenda_personal.clases_detalle(conn, est)),
            "telegram": dict(telegram.estado(conn, est), configurado=bool(os.getenv("TELEGRAM_BOT_TOKEN")))}


@app.route("/api/plan")
def api_plan():
    return _con_estudiante(_vista_plan)


@app.route("/api/plan/preferencias", methods=["POST"])
def api_plan_preferencias():
    def guardar(conn, est, d):
        from core import preferencias
        preferencias.guardar_todo(conn, est, d)
        return _vista_plan(conn, est)
    return _con_estudiante(guardar)


# ---------------------------------------------------------------- horario personal y Telegram
@app.route("/api/agenda", methods=["POST"])
def api_agenda():
    """Guarda a mano las franjas libres y/o las actividades (el asistente usa las mismas funciones)."""
    def guardar(conn, est, d):
        from core import agenda_personal
        if "franjas" in d:
            agenda_personal.guardar_franjas(conn, est, d["franjas"])
        if "actividades" in d:
            agenda_personal.guardar_actividades(conn, est, d["actividades"])
        return _vista_plan(conn, est)
    return _con_estudiante(guardar)


@app.route("/api/agenda/agregar", methods=["POST"])
def api_agenda_agregar():
    """Agrega rutina (actividades) u horas libres (franjas) sin borrar lo anterior."""
    def hacer(conn, est, d):
        from core import agenda_personal
        for tipo, clave in (("actividad", "actividades"), ("franja", "franjas")):
            if clave in d:
                agenda_personal.agregar(conn, est, tipo, d[clave])
        return _vista_plan(conn, est)
    return _con_estudiante(hacer)


@app.route("/api/agenda/editar", methods=["POST"])
def api_agenda_editar():
    def hacer(conn, est, d):
        from core import agenda_personal
        agenda_personal.editar(conn, est, d.get("tipo"), d.get("id"), d.get("item"))
        return _vista_plan(conn, est)
    return _con_estudiante(hacer)


@app.route("/api/agenda/borrar", methods=["POST"])
def api_agenda_borrar():
    def hacer(conn, est, d):
        from core import agenda_personal
        agenda_personal.borrar(conn, est, d.get("tipo"), d.get("id"))
        return _vista_plan(conn, est)
    return _con_estudiante(hacer)


@app.route("/api/plan/mover", methods=["POST"])
def api_plan_mover():
    """Mueve y fija un bloque de estudio: {clave, desde:{fecha, inicio}, fecha, inicio}."""
    def hacer(conn, est, d):
        from core import bloques_fijos
        desde = d.get("desde") if isinstance(d.get("desde"), dict) else {}
        bloques_fijos.mover(conn, est, d.get("clave"), desde.get("fecha"), desde.get("inicio"), d.get("fecha"), d.get("inicio"))
        return _vista_plan(conn, est)
    return _con_estudiante(hacer)


@app.route("/api/plan/soltar", methods=["POST"])
def api_plan_soltar():
    """Quita fijados (uno con clave+fecha+inicio, o todos si el cuerpo viene vacio)."""
    def hacer(conn, est, d):
        from core import bloques_fijos
        bloques_fijos.soltar(conn, est, d.get("clave"), d.get("fecha"), d.get("inicio"))
        return _vista_plan(conn, est)
    return _con_estudiante(hacer)


@app.route("/api/telegram/vincular", methods=["POST"])
def api_telegram_vincular():
    def crear(conn, est, _d):
        from core import telegram
        if not os.getenv("TELEGRAM_BOT_TOKEN"):
            raise ValueError("Los recordatorios por Telegram no están activados en este servidor.")
        try:
            bot = telegram.usuario_bot(os.getenv("TELEGRAM_BOT_USERNAME"), os.getenv("TELEGRAM_BOT_TOKEN"))
        except telegram.TelegramError as e:
            raise ValueError(f"No pude hablar con Telegram: {e}. Revisa TELEGRAM_BOT_TOKEN en el .env y tu conexión.") from None
        if not bot:
            raise ValueError("El bot de Telegram todavía no está listo. Revisa la terminal del servidor.")
        codigo = telegram.crear_codigo(conn, est)
        return {"enlace": f"https://t.me/{bot}?start={codigo}", "bot": bot, "vence_min": telegram.CODIGO_VIGENCIA_MIN}
    return _con_estudiante(crear)


@app.route("/api/telegram/preferencias", methods=["POST"])
def api_telegram_preferencias():
    def guardar(conn, est, d):
        from core import telegram
        telegram.guardar_preferencias(conn, est, d.get("activo"), d.get("avisar_min"))
        return _vista_plan(conn, est)["telegram"]
    return _con_estudiante(guardar)


@app.route("/api/telegram/desvincular", methods=["POST"])
def api_telegram_desvincular():
    def borrar(conn, est, _d):
        from core import telegram
        telegram.desvincular(conn, est)
        return _vista_plan(conn, est)["telegram"]
    return _con_estudiante(borrar)


@app.route("/api/plan/excluir", methods=["POST"])
def api_plan_excluir():
    def f(conn, est, d):
        from core import preferencias
        preferencias.excluir(conn, est, d.get("clave"))
        return _vista_plan(conn, est)
    return _con_estudiante(f)


@app.route("/api/plan/incluir", methods=["POST"])
def api_plan_incluir():
    def f(conn, est, d):
        from core import preferencias
        preferencias.incluir(conn, est, d.get("clave") or "")
        return _vista_plan(conn, est)
    return _con_estudiante(f)


@app.route("/api/resumen_semanal", methods=["POST"])
def api_resumen_semanal():
    """Genera (con IA) el resumen de la semana a partir de datos ya verificados.
    Se pide a propósito, no en cada carga del panel: tiene costo y demora."""
    def f(conn, est, _d):
        from core import resumen_semanal
        return resumen_semanal.generar(conn, est)
    return _con_estudiante(f)


@app.route("/api/alertas")
def api_alertas():
    def f(conn, est, _d):
        from core import alertas
        alertas.generar(conn, est)
        return {"alertas": alertas.activas(conn, est)}
    return _con_estudiante(f)


@app.route("/api/alertas/vistas", methods=["POST"])
def api_alertas_vistas():
    def f(conn, est, d):
        from core import alertas
        ids = d.get("ids")
        if ids is not None and (not isinstance(ids, list) or not all(isinstance(i, int) and not isinstance(i, bool) for i in ids)):
            raise ValueError("Alertas: formato no válido.")
        n = alertas.marcar_vistas(conn, est, ids)
        return {"marcadas": n, "alertas": alertas.activas(conn, est)}
    return _con_estudiante(f)


@app.route("/carnet")
def carnet():
    """
    Descarga el carne estudiantil en PDF. No se guarda nada: se pide al ERP con el
    token de la sesion del estudiante y se le entrega al navegador.
    """
    sid, sesion = get_session()
    if not sesion:
        return jsonify({"error": "No has iniciado sesión"}), 401
    ok, espera = limites.revisar("carnet", sid)
    if not ok:
        return _demasiadas(espera)
    if not sesion.get("sga_access_token"):
        return jsonify({"error": "El carné viene del ERP y entraste solo con Moodle. "
                                 "Cierra sesión y vuelve a entrar conectando el ERP (es opcional)."}), 400
    renovar_token_erp(sid, sesion)
    sga = SGAClient(
        base_url=os.getenv("SGA_BASE_URL", "https://sga.unemi.edu.ec"),
        access_token=sesion.get("sga_access_token"),
        refresh_token=sesion.get("sga_refresh_token"),
    )
    try:
        with limites.cupo_sync.tomar(5):
            try:
                sga.cambiar_a_perfil_regular()
            except Exception:
                pass
            info = sga.get_carnet_parseado()
            sesion["sga_access_token"], sesion["sga_refresh_token"] = sga.access_token, sga.refresh_token
            sessions_store.guardar(sid, sesion)
            url = info.get("pdf_url") or ""
            if url.startswith("/"):
                url = os.getenv("SGA_BASE_URL", "https://sga.unemi.edu.ec").rstrip("/") + url

            if url:
                # Caso normal: el ERP ya entrega el PDF real (el mismo que arma su boton
                # "Imprimir en PDF"), asi que lo bajamos tal cual y se lo pasamos al navegador.
                partes = urlparse(url)
                if partes.scheme != "https" or (partes.hostname or "").lower() not in {"sga.unemi.edu.ec", "sgaestudiante.unemi.edu.ec"}:
                    return jsonify({"error": "El enlace del carné no es de un dominio permitido."}), 400
                resp = requests.get(url, headers={"Authorization": f"Bearer {sga.access_token}"}, timeout=20)
                resp.raise_for_status()
                pdf_bytes = resp.content
            elif info.get("cedula") or info.get("carrera"):
                # Caso actual (nov/2026): el ERP solo entrega los datos del "carne digital"
                # (ver SGAClient.get_carnet_parseado); AcademIA arma el PDF con esos mismos datos.
                foto_bytes = None
                if info.get("foto_url"):
                    foto_bytes, _tipo, _motivo = foto.descargar_erp(
                        info["foto_url"], sga.access_token,
                        os.getenv("SGA_BASE_URL", "https://sga.unemi.edu.ec"))
                pdf_bytes = carnet_pdf.generar_pdf(info, foto_bytes)
            else:
                return jsonify({"error": "No hay un carné generado para esta cuenta en el ERP."}), 404

            return Response(pdf_bytes, mimetype="application/pdf",
                            headers={"Content-Disposition": 'attachment; filename="carne-unemi.pdf"'})
    except limites.Ocupado:
        return jsonify({"error": "Hay muchas operaciones en curso. Inténtalo de nuevo en un momento."}), 503
    except Exception as e:
        return jsonify({"error": f"No se pudo obtener el carné: {depurar(e)}"}), 502


# ---------------------------------------------------------------------------
# Mis datos: ver, descargar y borrar lo que se guardo sobre el estudiante.
# ---------------------------------------------------------------------------
@app.route("/api/mis-datos")
def api_mis_datos():
    sid, sesion = get_session()
    if not sesion:
        return jsonify({"error": "No has iniciado sesión"}), 401
    return jsonify(mis_datos.resumen(sesion["estudiante_id"]))


@app.route("/api/mis-datos/exportar")
def api_mis_datos_exportar():
    """Descarga un JSON con todo lo que la app guarda del estudiante (derecho de acceso). No incluye tokens ni chat_id de Telegram."""
    sid, sesion = get_session()
    if not sesion:
        return jsonify({"error": "No has iniciado sesión"}), 401
    ok, espera = limites.revisar("estudio", sid)
    if not ok:
        return _demasiadas(espera)
    import json as _json
    cuerpo = _json.dumps(mis_datos.exportar(sesion["estudiante_id"]), ensure_ascii=False, indent=2)
    return Response(cuerpo, mimetype="application/json",
                    headers={"Content-Disposition": 'attachment; filename="mis-datos-academia.json"'})


@app.route("/api/mis-datos/borrar", methods=["POST"])
def api_mis_datos_borrar():
    """Borra todo lo guardado sobre el estudiante y cierra su sesion."""
    sid, sesion = get_session()
    if not sesion:
        return jsonify({"error": "No has iniciado sesión"}), 401
    mis_datos.borrar(sesion["estudiante_id"])
    sessions_store.eliminar(sid)
    ESTUDIOS.pop(sid, None)
    asistente = ASSISTANTS.pop(sid, None)
    if asistente:
        asistente.close()
    resp = jsonify({"ok": True})
    resp.delete_cookie(COOKIE_NAME)
    return resp


@app.route("/proxy-image")
def proxy_image():
    """
    Trae una imagen del SGA usando el token de sesion del usuario actual,
    y la sirve desde nuestro propio dominio. Esto evita el problema de
    que el navegador intente cargar la imagen directo desde sga.unemi.edu.ec
    (dominio distinto al de nuestra app), que puede fallar por CORS o por
    requerir la sesion/token que solo nuestro backend tiene guardado.

    Por seguridad, solo se permite proxy-ear imagenes que vengan de
    dominios del ERP academico institucional (evita que esta ruta se use para traer cualquier
    URL arbitraria de internet).
    """
    sid, sesion = get_session()
    if not sesion:
        return "No autorizado", 401

    url = request.args.get("url", "")
    # Se compara el HOST exacto de la URL (no un "contiene el texto"): de lo
    # contrario, una URL como https://sitio-malo.com/?x=sga.unemi.edu.ec
    # pasaria el filtro y la app le enviaria el token del estudiante.
    dominios_permitidos = {"sga.unemi.edu.ec", "sgaestudiante.unemi.edu.ec"}
    partes = urlparse(url)
    if partes.scheme != "https" or (partes.hostname or "").lower() not in dominios_permitidos:
        return "URL no permitida", 400

    try:
        resp = requests.get(
            url,
            headers={"Authorization": f"Bearer {sesion['sga_access_token']}"},
            timeout=10,
        )
        resp.raise_for_status()
        return Response(resp.content, content_type=resp.headers.get("Content-Type", "image/png"))
    except Exception as e:
        return f"No se pudo cargar la imagen: {depurar(e)}", 502


@app.route("/api/chat", methods=["POST"])
def chat():
    """Endpoint del chatbot: aplica limites y presupuesto de IA, toma un cupo y responde con texto, acciones (botones), opciones de encuesta y las fuentes de los datos."""
    sid, sesion = get_session()
    if not sesion:
        return jsonify({"error": "No has iniciado sesión"}), 401

    data = request.get_json(force=True)
    pregunta = (data.get("pregunta") or "").strip()
    if not pregunta:
        return jsonify({"error": "Pregunta vacía"}), 400

    if not os.getenv("ANTHROPIC_API_KEY"):
        return jsonify({"error": "Falta ANTHROPIC_API_KEY en el archivo .env"}), 500

    ok, espera = limites.revisar("chat", sid)
    if not ok:
        return _demasiadas(espera)
    if not limites.presupuesto_ia.consumir():
        return jsonify({"error": "Hoy se alcanzó el límite de uso de la IA. Vuelve mañana."}), 429
    try:
        asistente = get_asistente_para(sid, sesion["estudiante_id"])
        with limites.cupo_ia.tomar(5), asistente.lock:
            respuesta = asistente.preguntar(pregunta)
            usadas = list(asistente.herramientas_usadas)
            acciones = list(getattr(asistente, "acciones", []))
            opciones = getattr(asistente, "opciones", None)
        return jsonify({
            "respuesta": respuesta,
            "acciones": acciones,   # ej. ["carnet"]: la interfaz muestra el boton de descarga
            "opciones": opciones,   # pregunta con botones (o None)
            # De donde salieron los datos de esta respuesta (y cuando se sincronizaron).
            "fuentes": panel.fuentes_de(usadas, sesion["estudiante_id"]),
        })
    except limites.Ocupado:
        return jsonify({"error": "El asistente está muy ocupado. Inténtalo de nuevo en unos segundos."}), 503
    except Exception as e:
        return jsonify({"error": depurar(e)}), 500


# ---------------------------------------------------------------------------
# Espacio de estudio: practica por materia, videos y tutor de conceptos.
# Reglas en core/estudio.py (no resuelve evaluaciones; videos solo educativos).
# ---------------------------------------------------------------------------
def get_estudio_para(sid: str, estudiante_id: int) -> Estudio:
    if sid not in ESTUDIOS:
        _guardar_acotado(ESTUDIOS, sid, Estudio(estudiante_id))
    return ESTUDIOS[sid]


def _estudio_api(accion, usa_ia: bool = True):
    """Ejecuta una accion del espacio de estudio con sesion, limites y errores en JSON."""
    sid, sesion = get_session()
    if not sesion:
        return jsonify({"error": "No has iniciado sesión"}), 401
    if usa_ia:
        if not os.getenv("ANTHROPIC_API_KEY"):
            return jsonify({"error": "Falta ANTHROPIC_API_KEY en el archivo .env"}), 500
        ok, espera = limites.revisar("estudio", sid)
        if not ok:
            return _demasiadas(espera)
        if not limites.presupuesto_ia.consumir():
            return jsonify({"error": "Hoy se alcanzó el límite de uso de la IA. Vuelve mañana."}), 429
    estudio = get_estudio_para(sid, sesion["estudiante_id"])
    datos = request.get_json(silent=True) or {}
    try:
        cupo = limites.cupo_ia.tomar(5) if usa_ia else nullcontext()
        with cupo, estudio.lock:
            return jsonify(accion(estudio, datos))
    except limites.Ocupado:
        return jsonify({"error": "El espacio de estudio está muy ocupado. Inténtalo de nuevo en unos segundos."}), 503
    except ValueError as e:     # entradas invalidas o pedidos que el espacio rechaza
        return jsonify({"error": str(e)}), 400


@app.route("/salud")
def salud():
    """Lo consulta el hosting para saber si la app esta viva. No toca la base ni la sesion."""
    return jsonify({"ok": True})


@app.route("/estudio")
def estudio_pagina():
    sid, sesion = get_session()
    if not sesion:
        return redirect("/login")
    return render_template("estudio.html", nombre=sesion.get("nombre", ""))


@app.route("/api/estudio/materias")
def estudio_materias():
    sid, sesion = get_session()
    if not sesion:
        return jsonify({"error": "No has iniciado sesión"}), 401
    return jsonify({"materias": get_estudio_para(sid, sesion["estudiante_id"]).materias()})


@app.route("/api/estudio/practica", methods=["POST"])
def estudio_practica():
    return _estudio_api(lambda e, d: e.iniciar_practica(
        d.get("materia_id"), d.get("tema", ""), d.get("n", 5), d.get("dificultad", "medio")))


@app.route("/api/estudio/responder", methods=["POST"])
def estudio_responder():
    return _estudio_api(lambda e, d: e.responder(d.get("indice"), d.get("opcion")), usa_ia=False)


@app.route("/api/estudio/videos", methods=["POST"])
def estudio_videos():
    return _estudio_api(lambda e, d: {"videos": e.videos(d.get("materia_id"), d.get("tema", ""))})


@app.route("/api/estudio/tutor", methods=["POST"])
def estudio_tutor():
    return _estudio_api(lambda e, d: e.tutor(d.get("materia_id"), d.get("mensaje", "")))


@app.route("/api/estudio/tutor/limpiar", methods=["POST"])
def estudio_tutor_limpiar():
    sid, sesion = get_session()
    if not sesion:
        return jsonify({"error": "No has iniciado sesión"}), 401
    get_estudio_para(sid, sesion["estudiante_id"]).limpiar_tutor()
    return jsonify({"ok": True})


@app.route("/api/chat/limpiar", methods=["POST"])
def chat_limpiar():
    """Borra la memoria de la conversacion del asistente de esta sesion."""
    sid, sesion = get_session()
    if not sesion:
        return jsonify({"error": "No has iniciado sesión"}), 401
    ASSISTANTS.pop(sid, None)
    return jsonify({"ok": True})


def _iniciar_recordatorios():
    """Hilo del bot de Telegram (solo si hay TELEGRAM_BOT_TOKEN; con recarga de debug, solo en el proceso hijo)."""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        return
    if os.getenv("FLASK_DEBUG") == "1" and os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        return
    from core.trabajador import Trabajador
    Trabajador(token).start()


if __name__ == "__main__":
    puerto = int(os.getenv("PORT", "5000"))
    host = os.getenv("HOST", "127.0.0.1")
    _iniciar_recordatorios()

    if os.getenv("FLASK_DEBUG") == "1":
        # Solo para desarrollo: recarga sola al editar codigo. NUNCA en un
        # servidor real (el modo debug permite ejecutar codigo desde el navegador).
        print(f"AcademIA (MODO DESARROLLO) en http://{host}:{puerto}")
        app.run(host=host, port=puerto, debug=True)
    else:
        try:
            from waitress import serve
        except ImportError:
            raise SystemExit(
                "Falta 'waitress' (servidor de produccion). Instala las dependencias con:\n"
                "    pip install -r requirements.txt\n"
                "o, solo para desarrollo, arranca con FLASK_DEBUG=1."
            )
        hilos = int(os.getenv("THREADS", "48"))
        limites.advertir_hilos(hilos)
        print(f"AcademIA (multi-usuario) corriendo en http://{host}:{puerto} con {hilos} hilos")
        # threads = peticiones que se atienden a la vez; connection_limit = conexiones abiertas
        # que acepta; channel_timeout = cierra conexiones inactivas para no acumularlas.
        serve(app, host=host, port=puerto, threads=hilos,
              connection_limit=int(os.getenv("CONNECTION_LIMIT", "500")),
              channel_timeout=int(os.getenv("CHANNEL_TIMEOUT", "60")))
