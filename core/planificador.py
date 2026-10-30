"""
Plan de estudio explicable (Fase 3).

Problema que resuelve: la persona tiene varias entregas y evaluaciones a la vez y poco tiempo;
Moodle y el ERP muestran fechas sueltas, pero no ayudan a repartir el esfuerzo.

Como funciona (todo visible para el estudiante, nada de modelos ocultos):
  1. Entra al plan lo que esta CONFIRMADO como pendiente (tareas sin entregar y vencidas sin
     entregar) y las evaluaciones del calendario. Lo "sin verificar" se lista aparte: no se planifica.
  2. Cada elemento necesita minutos de estudio = minutos base (que la persona ajusta) x un factor
     por la DIFICULTAD que ella indico para esa materia (facil 0.75, media 1, dificil 1.5).
     Son estimaciones, no mediciones, y el plan lo dice.
  3. El tiempo disponible por dia lo escribe la persona. Si no lo ha indicado, no se supone nada.
  4. Se reparte lo mas urgente primero, dia a dia, sin pasar de la fecha limite. Si algo no cabe antes
     de vencer, se dice cuantos minutos faltan en vez de esconderlo.
  5. Cada bloque trae su "motivo". Lo que la persona quita del plan se puede volver a incluir.
"""
import re
from datetime import timedelta

from core import preferencias as P
from core.cambios import _fecha_humana
from core.tareas import clasificar_tareas
from core.tiempo import ahora_iso, iso_a_datetime

DIAS_PLAN = 7
BLOQUE_MIN = 15
FACTOR = {1: 0.75, 2: 1.0, 3: 1.5}
NOMBRE_DIFICULTAD = {1: "fácil", 2: "media", 3: "difícil"}
VENTANA_SIN_VERIFICAR = 30
# Eventos del calendario que parecen evaluaciones (se puede quitar cualquiera del plan).
_PARECE_EVALUACION = re.compile(r"examen|prueba|evaluaci|quiz|\btest\b|parcial|lecci[oó]n", re.I)


def _redondear(minutos: float) -> int:
    return max(BLOQUE_MIN, int(round(minutos / BLOQUE_MIN)) * BLOQUE_MIN)


def _fecha(iso):
    return iso_a_datetime(iso).date()


def _elementos(conn, est, ahora, prefs, dif, grupos):
    """(elementos, sin_verificar). Cada elemento: clave, nombre, materia, materia_id, fecha, tipo, vencida, minutos, base."""
    def minutos(base, materia_id):
        nivel = dif.get(materia_id, 2)
        return _redondear(base * FACTOR[nivel]), nivel

    elementos = []
    for grupo, vencida in (("vencidas_sin_entregar", True), ("pendientes", False)):
        for t in grupos[grupo]:
            m, nivel = minutos(prefs["minutos_por_tarea"], t["materia_id"])
            elementos.append({"clave": f"t:{t['tarea_id']}", "nombre": t["tarea"], "materia": t["materia"],
                              "materia_id": t["materia_id"], "fecha": t["fecha"], "tipo": "tarea",
                              "vencida": vencida, "minutos": m, "base": prefs["minutos_por_tarea"],
                              "nivel": nivel, "indicada": t["materia_id"] in dif})
    for e in conn.execute("SELECT nombre, fecha, materia_id FROM evento WHERE estudiante_id = ? AND fecha >= ? ORDER BY fecha",
                          (est, ahora)):
        if not _PARECE_EVALUACION.search(e["nombre"] or ""):
            continue
        m, nivel = minutos(prefs["minutos_evaluacion"], e["materia_id"])
        elementos.append({"clave": f"e:{e['nombre']}|{e['fecha']}", "nombre": e["nombre"], "materia": None,
                          "materia_id": e["materia_id"], "fecha": e["fecha"], "tipo": "evaluacion",
                          "vencida": False, "minutos": m, "base": prefs["minutos_evaluacion"],
                          "nivel": nivel, "indicada": e["materia_id"] in dif})
    corte = (iso_a_datetime(ahora) - timedelta(days=VENTANA_SIN_VERIFICAR)).replace(tzinfo=None).isoformat(timespec="seconds")
    sin_verificar = [t for t in grupos["sin_verificar"] if t["fecha"] is None or t["fecha"] >= corte]
    return elementos, sin_verificar


def _motivo(e):
    dificultad = (f"dificultad {NOMBRE_DIFICULTAD[e['nivel']]}" if e["indicada"]
                  else "dificultad media (no la indicaste)")
    estimado = f"Estimado: {e['minutos']} min ({e['base']} min base, {dificultad})."
    if e["vencida"]:
        return f"Ya venció y Moodle la indica sin entregar; conviene resolverla primero. {estimado}"
    if e["tipo"] == "evaluacion":
        return f"Es el {_fecha_humana(e['fecha'])}. {estimado}"
    if e["fecha"]:
        return f"Vence el {_fecha_humana(e['fecha'])}. {estimado}"
    return f"No tiene fecha de entrega. {estimado}"


def generar(conn, estudiante_id: int, ahora: str = None, dias: int = DIAS_PLAN) -> dict:
    ahora = ahora or ahora_iso("seconds")
    prefs, dif, excluidos = P.obtener(conn, estudiante_id), P.dificultades(conn, estudiante_id), P.excluidos(conn, estudiante_id)
    grupos = clasificar_tareas(conn, estudiante_id, ahora=ahora)
    elementos, sin_verificar = _elementos(conn, estudiante_id, ahora, prefs, dif, grupos)

    hoy = _fecha(ahora)
    calendario = [hoy + timedelta(days=i) for i in range(dias)]
    capacidad = {d: prefs["disponibilidad"].get(P.DIAS[d.weekday()], 0) for d in calendario}
    disponible = dict(capacidad)
    configurado = any(capacidad.values())

    quitados = [e for e in elementos if e["clave"] in excluidos]
    activos = [e for e in elementos if e["clave"] not in excluidos]

    def limite(e):
        """Ultimo dia en que se puede estudiar esto (None = sin limite dentro del plan)."""
        if e["vencida"] or not e["fecha"]:
            return None
        f = _fecha(e["fecha"])
        return f - timedelta(days=1) if e["tipo"] == "evaluacion" and f > hoy else f

    en_plan, fuera = [], []
    for e in activos:
        lim = limite(e)
        (fuera if lim is not None and lim > calendario[-1] else en_plan).append(e)

    def orden(e):
        grupo = 0 if e["vencida"] else (2 if not e["fecha"] else 1)
        return (grupo, e["fecha"] or "", e["nombre"])
    en_plan.sort(key=orden)

    bloques = {d: [] for d in calendario}
    no_alcanza, sin_espacio = [], []
    if configurado:
        for e in en_plan:
            falta, lim = e["minutos"], limite(e)
            for d in calendario:
                if falta <= 0:
                    break
                if lim is not None and d > lim:
                    break
                toma = min(falta, disponible[d])
                if toma < BLOQUE_MIN and toma < falta:
                    continue
                if toma <= 0:
                    continue
                disponible[d] -= toma
                falta -= toma
                bloques[d].append({"clave": e["clave"], "item": e["nombre"], "materia": e["materia"],
                                   "minutos": toma, "tipo": e["tipo"], "motivo": _motivo(e)})
            if falta > 0:
                dato = {"clave": e["clave"], "item": e["nombre"], "materia": e["materia"], "fecha": e["fecha"],
                        "faltan_min": falta, "total_min": e["minutos"]}
                (no_alcanza if e["fecha"] or e["vencida"] else sin_espacio).append(dato)

    return {
        "configurado": configurado,
        "dias": [{"fecha": d.isoformat(), "dia": P.DIAS[d.weekday()], "disponible_min": capacidad[d],
                  "usado_min": capacidad[d] - disponible[d], "bloques": bloques[d]} for d in calendario],
        "no_alcanza": no_alcanza,
        "sin_espacio": sin_espacio,
        "por_planificar": [{"clave": e["clave"], "item": e["nombre"], "materia": e["materia"], "minutos": e["minutos"]}
                           for e in en_plan] if not configurado else [],
        "fuera_del_plan": [{"clave": e["clave"], "item": e["nombre"], "fecha": e["fecha"]} for e in fuera],
        "sin_verificar": [{"item": t["tarea"], "materia": t["materia"], "fecha": t["fecha"]} for t in sin_verificar],
        "quitados": [{"clave": e["clave"], "item": e["nombre"], "fecha": e["fecha"]} for e in quitados],
        "total_necesario_min": sum(e["minutos"] for e in en_plan),
        "total_disponible_min": sum(capacidad.values()),
        "supuestos": [
            f"Cada tarea se estima en {prefs['minutos_por_tarea']} min y cada evaluación en {prefs['minutos_evaluacion']} min, "
            "ajustados por la dificultad que indicaste (fácil ×0,75, media ×1, difícil ×1,5). Son estimaciones tuyas, no mediciones.",
            "Solo se planifica lo que Moodle confirma como sin entregar y las evaluaciones del calendario; las tareas sin verificar no entran.",
            "Un evento cuenta como evaluación si su nombre lo parece (examen, prueba, parcial…); puedes quitar del plan cualquier elemento.",
        ],
    }
