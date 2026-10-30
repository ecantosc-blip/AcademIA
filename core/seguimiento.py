"""
Indicador de seguimiento (Fase 2): un conjunto de reglas SIMPLES y VISIBLES sobre datos ya
verificados. Responde "¿a qué debo prestar atención hoy?", sin predecir nada.

Problema que resuelve: Moodle y el ERP muestran los datos sueltos, pero nadie junta "esto vencio
y no se entrego", "esto vence pronto", "esto no pude confirmar" y "estos datos pueden estar
desactualizados" en un solo lugar.

Principios (probados en tests/test_seguimiento.py):
  * Cada regla dice QUE mide y POR QUE (campo `porque`): el estudiante puede revisar la logica.
  * Solo usa hechos guardados y verificados. Una tarea "sin verificar" nunca cuenta como
    pendiente ni como entregada: se informa aparte, como dato faltante.
  * No hay modelo predictivo ni "riesgo de reprobar". No se juzgan notas (Moodle y el ERP no
    entregan pesos ni maximos) ni se comparan con reglamentos (fuera del alcance acordado).
  * La asistencia se muestra como dato, sin veredicto: no hay un mínimo oficial confirmado.
    Un minimo propio se podra configurar en la Fase 3 (alertas configurables).
  * Si faltan datos se dice "sin datos"; nunca se rellena con una suposicion.
"""
from datetime import timedelta

from core import cambios
from core.tareas import clasificar_tareas
from core.tiempo import ahora_iso, iso_a_datetime

HORAS_PROXIMA = 48           # una tarea pendiente "vence pronto" si falta esto o menos
DIAS_DATOS_VIEJOS = 2        # lectura exitosa mas antigua que esto = datos posiblemente viejos
VENTANA_DIAS = 30            # igual que la consulta de entregas: no se mira lo mas antiguo
MAX_ITEMS = 5
_DE = {"moodle": "de Moodle", "sga": "del ERP"}      # "una lectura de Moodle" / "una lectura del ERP"


def _plural(n, uno, varios):
    return f"{n} {uno if n == 1 else varios}"


def _items(tareas):
    return [{"texto": f"{t['tarea']} · {t['materia']}", "fecha": t["fecha"]} for t in tareas[:MAX_ITEMS]]


def _regla(id_, titulo, estado, detalle, porque, items=None):
    return {"id": id_, "titulo": titulo, "estado": estado, "detalle": detalle, "porque": porque,
            "items": items or []}


def _regla_vencidas(grupos):
    v = grupos["vencidas_sin_entregar"]
    porque = ("Cuenta las tareas que Moodle informa como sin entregar (o en borrador) y cuya fecha ya pasó. "
              f"Solo se consultan tareas de los últimos {VENTANA_DIAS} días.")
    if v:
        return _regla("vencidas", "Tareas vencidas sin entregar", "atencion",
                      f"{_plural(len(v), 'tarea vencida', 'tareas vencidas')} que Moodle indica sin entregar.",
                      porque, _items(v))
    return _regla("vencidas", "Tareas vencidas sin entregar", "ok",
                  "Ninguna entre las tareas que pude verificar.", porque)


def _regla_proximas(grupos, ahora):
    limite = (iso_a_datetime(ahora) + timedelta(hours=HORAS_PROXIMA)).replace(tzinfo=None).isoformat(timespec="seconds")
    cerca = [t for t in grupos["pendientes"] if t["fecha"] is not None and t["fecha"] <= limite]
    porque = (f"Cuenta las tareas que Moodle informa como sin entregar (o en borrador) y que vencen en las "
              f"próximas {HORAS_PROXIMA} horas.")
    if cerca:
        return _regla("proximas", f"Vencen en {HORAS_PROXIMA} horas", "atencion",
                      f"{_plural(len(cerca), 'tarea sin entregar vence', 'tareas sin entregar vencen')} "
                      f"en las próximas {HORAS_PROXIMA} horas.", porque, _items(cerca))
    return _regla("proximas", f"Vencen en {HORAS_PROXIMA} horas", "ok",
                  f"Ninguna tarea confirmada como pendiente vence en las próximas {HORAS_PROXIMA} horas.", porque)


def _regla_sin_verificar(grupos, ahora):
    corte = (iso_a_datetime(ahora) - timedelta(days=VENTANA_DIAS)).replace(tzinfo=None).isoformat(timespec="seconds")
    sv = [t for t in grupos["sin_verificar"] if t["fecha"] is None or t["fecha"] >= corte]
    porque = ("Son tareas de las que Moodle no informó si están entregadas. No se cuentan ni como pendientes ni "
              "como entregadas: revísalas directamente en Moodle.")
    if sv:
        return _regla("sin_verificar", "Tareas sin verificar", "sin_datos",
                      f"No pude confirmar el estado de {_plural(len(sv), 'tarea', 'tareas')}.", porque, _items(sv))
    return _regla("sin_verificar", "Tareas sin verificar", "ok",
                  "El estado de todas las tareas recientes está confirmado.", porque)


def _regla_asistencia(conn, estudiante_id):
    filas = conn.execute("""
        SELECT m.nombre, mat.asistencia FROM matricula mat JOIN materia m ON m.id = mat.materia_id
        WHERE mat.estudiante_id = ? AND mat.estado = 'en_curso' AND mat.asistencia IS NOT NULL
        ORDER BY mat.asistencia ASC, m.nombre LIMIT 1
    """, (estudiante_id,)).fetchall()
    porque = ("Muestra la asistencia más baja entre tus materias en curso, tal como la informa el ERP. "
              "Es un dato, no un veredicto: no hay un mínimo oficial confirmado en esta aplicación.")
    if not filas:
        return _regla("asistencia", "Asistencia", "sin_datos", "El ERP no informó asistencia de tus materias en curso.", porque)
    f = filas[0]
    valor = f"{f['asistencia']:.0f}" if float(f["asistencia"]).is_integer() else f"{f['asistencia']:.1f}".replace(".", ",")
    return _regla("asistencia", "Asistencia", "info", f"Tu asistencia más baja es {valor}% en {f['nombre']}.", porque)


def _regla_novedades(conn, estudiante_id):
    n = cambios.contar_nuevas(conn, estudiante_id)
    porque = "Cambios detectados entre lecturas exitosas que todavía no marcaste como vistos."
    if n:
        return _regla("novedades", "Novedades", "info", f"{_plural(n, 'novedad sin ver', 'novedades sin ver')}.", porque)
    return _regla("novedades", "Novedades", "ok", "No hay novedades pendientes.", porque)


def _regla_datos(conn, estudiante_id, ahora):
    problemas, faltan = [], []
    limite = (iso_a_datetime(ahora) - timedelta(days=DIAS_DATOS_VIEJOS)).replace(tzinfo=None).isoformat(timespec="seconds")
    for plataforma, de in _DE.items():
        ultima = conn.execute("SELECT estado FROM sesion_extraccion WHERE estudiante_id = ? AND plataforma = ? "
                              "ORDER BY id DESC LIMIT 1", (estudiante_id, plataforma)).fetchone()
        ok = conn.execute("SELECT MAX(fecha) AS f FROM sesion_extraccion WHERE estudiante_id = ? AND plataforma = ? "
                          "AND estado = 'exitosa'", (estudiante_id, plataforma)).fetchone()["f"]
        if ultima is None:
            faltan.append(f"Aún no hay una lectura {de}.")
        elif ultima["estado"] == "fallida":
            problemas.append(f"La última lectura {de} falló; lo que ves puede estar desactualizado.")
        elif ok < limite:
            problemas.append(f"La última lectura buena {de} es de hace más de {DIAS_DATOS_VIEJOS} días.")
    porque = (f"Avisa si la última lectura de Moodle o del ERP falló, si no hay ninguna, o si la última exitosa tiene "
              f"más de {DIAS_DATOS_VIEJOS} días. Todo lo demás depende de que estos datos estén al día.")
    if problemas:
        return _regla("datos", "Datos al día", "atencion", " ".join(problemas + faltan), porque)
    if faltan:
        return _regla("datos", "Datos al día", "sin_datos", " ".join(faltan), porque)
    return _regla("datos", "Datos al día", "ok", "Moodle y el ERP se leyeron bien recientemente.", porque)


def evaluar(conn, estudiante_id: int, ahora: str = None) -> dict:
    """Devuelve {"resumen": {...}, "reglas": [...]}. `ahora` = ISO en hora de Ecuador (por defecto, ahora)."""
    ahora = ahora or ahora_iso("seconds")
    grupos = clasificar_tareas(conn, estudiante_id, ahora=ahora)
    reglas = [_regla_vencidas(grupos), _regla_proximas(grupos, ahora), _regla_sin_verificar(grupos, ahora),
              _regla_asistencia(conn, estudiante_id), _regla_novedades(conn, estudiante_id),
              _regla_datos(conn, estudiante_id, ahora)]
    atencion = sum(r["estado"] == "atencion" for r in reglas)
    sin_datos = sum(r["estado"] == "sin_datos" for r in reglas)
    if atencion:
        texto, nivel = f"{_plural(atencion, 'punto de atención', 'puntos de atención')}.", "atencion"
    elif sin_datos:
        texto, nivel = "Sin alertas, pero hay datos que no pude verificar.", "sin_datos"
    else:
        texto, nivel = "Sin puntos de atención en los datos que pude verificar.", "ok"
    return {"resumen": {"nivel": nivel, "texto": texto, "atencion": atencion, "sin_datos": sin_datos},
            "reglas": reglas}
