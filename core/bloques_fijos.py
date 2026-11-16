"""
Bloques de estudio que la persona mueve o fija a mano.

Lo que mueve queda FIJADO en esa fecha y hora; el planificador lo respeta y acomoda lo demas alrededor.
Solo se puede mover dentro de la semana del plan, de 07:00 a 24:00, no al pasado, y sin chocar con una
clase del ERP, con una actividad suya ni con otro bloque fijado.
"""
from datetime import date

from core import agenda_personal as AP
from core import planificador
from core.preferencias import DIAS
from core.tiempo import ahora_iso, iso_a_datetime

DIA_INI, DIA_FIN = 7 * 60, 24 * 60


def listar(conn, est) -> list:
    return [dict(r) for r in conn.execute(
        "SELECT clave, fecha, inicio, fin FROM bloque_fijo WHERE estudiante_id = ? ORDER BY fecha, inicio", (est,))]


def _hora(valor):
    try:
        return AP.a_minutos(valor)
    except ValueError:
        raise ValueError(f"«{valor}» no es una hora válida (usa el formato 18:30).") from None


def mover(conn, est, clave, desde_fecha, desde_inicio, fecha, inicio, ahora: str = None) -> dict:
    """Mueve (y fija) el bloque `clave` que hoy esta en desde_fecha/desde_inicio a fecha/inicio."""
    ahora = ahora or ahora_iso("seconds")
    plan = planificador.generar(conn, est, ahora=ahora)
    origen = next((b for d in plan["dias"] if d["fecha"] == desde_fecha for b in d["bloques"]
                   if b["clave"] == clave and b["inicio"] == desde_inicio), None)
    if origen is None:
        raise ValueError("Ese bloque ya no está en el plan (el horario cambió). Recarga y vuelve a intentarlo.")
    dias = [d["fecha"] for d in plan["dias"]]
    if fecha not in dias:
        raise ValueError("Solo puedes mover bloques dentro de los próximos 7 días del plan.")
    ini = _hora(inicio)
    fin = ini + origen["minutos"]
    if ini < DIA_INI or fin > DIA_FIN:
        raise ValueError("El bloque debe quedar entre las 07:00 y las 24:00.")
    ahora_dt = iso_a_datetime(ahora)
    if fecha == ahora_dt.date().isoformat() and ini < ahora_dt.hour * 60 + ahora_dt.minute:
        raise ValueError("Esa hora ya pasó.")
    dia = DIAS[date.fromisoformat(fecha).weekday()]
    for oi, of, nombre in AP.ocupado_por_dia(conn, est)[dia]:
        if ini < of and fin > oi:
            raise ValueError(f"Choca con «{nombre}» ({AP.a_texto(oi)}–{AP.a_texto(of)}).")
    with conn:
        conn.execute("DELETE FROM bloque_fijo WHERE estudiante_id = ? AND clave = ? AND fecha = ? AND inicio = ?",
                     (est, clave, desde_fecha, desde_inicio))
        otro = conn.execute("""SELECT clave, inicio, fin FROM bloque_fijo WHERE estudiante_id = ? AND fecha = ?""", (est, fecha)).fetchall()
        for o in otro:
            if ini < _hora(o["fin"]) and fin > _hora(o["inicio"]):
                raise ValueError("Choca con otro bloque que ya fijaste.")
        conn.execute("DELETE FROM bloque_fijo WHERE estudiante_id = ? AND clave = ? AND fecha = ? AND inicio = ?",
                     (est, clave, fecha, AP.a_texto(ini)))
        conn.execute("INSERT INTO bloque_fijo (estudiante_id, clave, fecha, inicio, fin) VALUES (?,?,?,?,?)",
                     (est, clave, fecha, AP.a_texto(ini), AP.a_texto(fin)))
    return {"clave": clave, "fecha": fecha, "inicio": AP.a_texto(ini), "fin": AP.a_texto(fin)}


def soltar(conn, est, clave=None, fecha=None, inicio=None) -> int:
    """Quita fijados: uno (clave+fecha+inicio), todos los de una clave o todos si no se indica nada."""
    sql, params = "DELETE FROM bloque_fijo WHERE estudiante_id = ?", [est]
    for col, v in (("clave", clave), ("fecha", fecha), ("inicio", inicio)):
        if v:
            sql += f" AND {col} = ?"
            params.append(v)
    with conn:
        return conn.execute(sql, params).rowcount
