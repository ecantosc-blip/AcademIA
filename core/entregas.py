"""
Estado de entrega de las tareas (Moodle: mod_assign_get_submission_status).

Principios:
  * No se inventa: si Moodle no responde o la respuesta no trae un estado reconocible,
    no se escribe nada (la tarea queda "sin verificar").
  * Un estado ya verificado NUNCA se pisa por un fallo posterior.
  * Las incidencias se registran como conteos y un mensaje ya depurado (sin tokens ni URL).

Hipotesis aun no confirmadas con una cuenta real (ver docs/VERIFICACION_MOODLE.md):
la funcion esta habilitada para el rol estudiante, y la respuesta trae
lastattempt.submission.status (o lastattempt.teamsubmission en tareas grupales).
"""
import os

from core.privacidad import depurar
from core.tiempo import ahora_iso

# Solo estados con significado claro. 'reopened' y cualquier otro se dejan sin verificar.
_ESTADOS = {"submitted": "entregada", "draft": "borrador", "new": "sin_entregar"}

MAX_CONSULTAS_POR_DEFECTO = 60
VENTANA_DIAS = 30            # se consultan tareas sin fecha, vigentes, o vencidas hace <= 30 dias
ABORTAR_TRAS_ERRORES = 3     # si las primeras N consultas fallan, la funcion no esta disponible


def interpretar_estado(respuesta):
    """Respuesta de Moodle -> 'entregada' | 'borrador' | 'sin_entregar' | None (sin verificar)."""
    try:
        ultimo = respuesta.get("lastattempt") if isinstance(respuesta, dict) else None
        if not isinstance(ultimo, dict):
            return None
        for clave in ("submission", "teamsubmission"):
            sub = ultimo.get(clave)
            if isinstance(sub, dict):
                estado = _ESTADOS.get(str(sub.get("status", "")).strip().lower())
                if estado:
                    return estado
        return None
    except Exception:
        return None


def _candidatas(tareas, existentes, ahora):
    corte = ahora_iso("seconds", days=-VENTANA_DIAS)
    validas = []
    for t in tareas:
        if t.get("sin_entrega_online"):
            continue                                   # tarea sin entrega en linea: Moodle no puede saberlo
        if existentes.get(t["tarea_id"]) == "entregada":
            continue                                   # ya confirmada: no se vuelve a preguntar
        f = t.get("fecha_entrega")
        if f is not None and f < corte:
            continue                                   # demasiado antigua
        validas.append(t)
    # primero lo vigente (mas cercano primero), despues lo vencido recientemente
    vigentes = sorted([t for t in validas if t.get("fecha_entrega") is None or t["fecha_entrega"] >= ahora],
                      key=lambda t: t.get("fecha_entrega") or "9999")
    vencidas = sorted([t for t in validas if t not in vigentes], key=lambda t: t["fecha_entrega"], reverse=True)
    return vigentes + vencidas


def sincronizar_entregas(cursor, moodle, userid, estudiante_id, tareas, max_consultas=None):
    """
    Consulta y guarda el estado de entrega. `tareas`: lista de dicts con
    tarea_id, assign_id, fecha_entrega (ISO o None) y sin_entrega_online (bool).
    No lanza excepciones por fallos de Moodle. Devuelve conteos y un texto para el registro.
    """
    if max_consultas is None:
        try:
            max_consultas = int(os.getenv("MAX_CONSULTAS_ENTREGA", MAX_CONSULTAS_POR_DEFECTO))
        except ValueError:
            max_consultas = MAX_CONSULTAS_POR_DEFECTO
    existentes = {r["tarea_id"]: r["estado"] for r in cursor.execute(
        "SELECT tarea_id, estado FROM entrega WHERE estudiante_id = ?", (estudiante_id,))}
    cola = _candidatas(tareas, existentes, ahora_iso("seconds"))
    omitidas_limite = max(0, len(cola) - max_consultas)
    cola = cola[:max_consultas]

    r = {"consultadas": 0, "verificadas": 0, "sin_estado": 0, "errores": 0,
         "omitidas_por_limite": omitidas_limite, "abortada": False, "primer_error": None}
    for t in cola:
        r["consultadas"] += 1
        try:
            respuesta = moodle.get_submission_status(t["assign_id"], userid)
        except Exception as e:
            r["errores"] += 1
            if r["primer_error"] is None:
                r["primer_error"] = depurar(e)[:140]
            if r["verificadas"] + r["sin_estado"] == 0 and r["errores"] >= ABORTAR_TRAS_ERRORES:
                r["abortada"] = True                   # la funcion no responde: no seguir insistiendo
                break
            continue
        estado = interpretar_estado(respuesta)
        if estado is None:
            r["sin_estado"] += 1                       # no se escribe nada: queda sin verificar
            continue
        cursor.execute("""
            INSERT INTO entrega (tarea_id, estudiante_id, estado, verificada_en) VALUES (?, ?, ?, ?)
            ON CONFLICT(tarea_id, estudiante_id) DO UPDATE SET
                estado = excluded.estado, verificada_en = excluded.verificada_en
        """, (t["tarea_id"], estudiante_id, estado, ahora_iso("seconds")))
        r["verificadas"] += 1

    partes = [f"entregas verificadas: {r['verificadas']}"]
    if r["sin_estado"]:
        partes.append(f"sin estado reconocible: {r['sin_estado']}")
    if r["errores"]:
        partes.append(f"errores de consulta: {r['errores']}")
    if r["abortada"]:
        partes.append("consulta de entregas no disponible, se detuvo")
    if omitidas_limite:
        partes.append(f"pendientes de consultar (limite): {omitidas_limite}")
    if r["primer_error"]:
        partes.append(f"primer error: {r['primer_error']}")
    r["detalle"] = "; ".join(partes)
    return r
