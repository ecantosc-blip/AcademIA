"""
Criterio UNICO para decidir que tareas estan pendientes, vencidas, entregadas o
"sin verificar". Lo usan el chat, el panel, el espacio de estudio y Mis datos, para que
nunca se contradigan.

Reglas (el estado sale de la tabla `entrega`, que llena Moodle):
  * 'entregada'                                  -> entregadas
  * 'sin_entregar' o 'borrador', no vencida      -> pendientes
  * 'sin_entregar' o 'borrador', ya vencida      -> vencidas_sin_entregar
  * SIN fila en `entrega` (Moodle no informo)    -> sin_verificar   (nunca pendiente ni entregada)
Una tarea sin fecha de entrega no esta vencida.
"""
from core.tiempo import ahora_iso

ESTADOS_SIN_ENTREGAR = ("sin_entregar", "borrador")


def clasificar_tareas(conn, estudiante_id: int, materia_id: int = None, ahora: str = None) -> dict:
    """
    Devuelve {"pendientes", "vencidas_sin_entregar", "sin_verificar", "entregadas"}, cada
    uno una lista de dicts: tarea_id, tarea, materia_id, materia, fecha (ISO o None),
    estado (None si no se verifico), verificada_en, vencida (bool).
    `ahora` es un ISO en hora de Ecuador (por defecto, el momento actual).
    """
    ahora = ahora or ahora_iso("seconds")
    params = [estudiante_id, estudiante_id]
    filtro = ""
    if materia_id is not None:
        filtro = "AND t.materia_id = ?"
        params.append(materia_id)
    filas = conn.execute(f"""
        SELECT t.id AS tarea_id, t.nombre AS tarea, m.id AS materia_id, m.nombre AS materia,
               t.fecha_entrega AS fecha, e.estado AS estado, e.verificada_en AS verificada_en
        FROM tarea t
        JOIN materia m ON m.id = t.materia_id
        LEFT JOIN entrega e ON e.tarea_id = t.id AND e.estudiante_id = ?
        WHERE EXISTS (SELECT 1 FROM matricula mat WHERE mat.materia_id = t.materia_id AND mat.estudiante_id = ?)
        {filtro}
    """, params).fetchall()

    grupos = {"pendientes": [], "vencidas_sin_entregar": [], "sin_verificar": [], "entregadas": []}
    for f in filas:
        d = dict(f)
        d["vencida"] = d["fecha"] is not None and d["fecha"] < ahora
        if d["estado"] == "entregada":
            grupos["entregadas"].append(d)
        elif d["estado"] in ESTADOS_SIN_ENTREGAR:
            grupos["vencidas_sin_entregar" if d["vencida"] else "pendientes"].append(d)
        else:
            grupos["sin_verificar"].append(d)

    def por_fecha(d):               # por fecha; sin fecha al final
        return (d["fecha"] is None, d["fecha"] or "")
    grupos["pendientes"].sort(key=por_fecha)
    grupos["sin_verificar"].sort(key=lambda d: (d["vencida"],) + por_fecha(d))
    grupos["vencidas_sin_entregar"].sort(key=lambda d: d["fecha"], reverse=True)
    grupos["entregadas"].sort(key=por_fecha)
    return grupos


def en_ventana(tareas: list, desde: str, hasta: str) -> list:
    """Tareas con fecha entre `desde` y `hasta` (ISO, ambos incluidos)."""
    return [t for t in tareas if t["fecha"] is not None and desde <= t["fecha"] <= hasta]
