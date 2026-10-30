"""
Alertas dentro de la app (Fase 3): configurables y sin duplicados.

Problema que resuelve: el estudiante no entra a Moodle todos los dias; la app le avisa de lo que
requiere atencion (entregas por vencer, vencidas, lectura fallida, asistencia bajo su minimo).

Garantias (probadas en tests/test_alertas.py):
  * Sin duplicados: cada alerta tiene una clave (p. ej. tarea + fecha) y la tabla impide repetirla,
    aunque se genere mil veces. Si el estudiante la descarta, NO vuelve a aparecer; si la fecha de la
    tarea cambia, es otra alerta y si avisa.
  * Siempre reflejan la situacion actual: una alerta aun no vista cuya causa ya no existe (tarea
    entregada, lectura recuperada, tipo desactivado) se retira sola.
  * Solo usan hechos verificados: una tarea "sin verificar" nunca genera alerta de entrega.
  * Lo que se avisa y con cuanta anticipacion lo elige la persona (core/preferencias.py).
"""
from datetime import timedelta

from core import preferencias as P
from core.cambios import _fecha_humana
from core.tareas import clasificar_tareas
from core.tiempo import ahora_iso, iso_a_datetime

VENTANA_DIAS = 30        # no se avisa de tareas vencidas hace mas de esto (igual que la lectura de entregas)
CONSERVAR_VISTAS_DIAS = 60
LIMITE_LISTA = 20
_DE = {"moodle": "de Moodle", "sga": "del ERP"}


def _candidatas(conn, est, ahora, prefs):
    horas = prefs["alerta_horas"]
    iso = lambda dt: dt.replace(tzinfo=None).isoformat(timespec="seconds")
    limite = iso(iso_a_datetime(ahora) + timedelta(hours=horas))
    corte = iso(iso_a_datetime(ahora) - timedelta(days=VENTANA_DIAS))
    grupos = clasificar_tareas(conn, est, ahora=ahora)
    cand = {}
    for t in grupos["pendientes"]:
        if t["fecha"] and t["fecha"] <= limite:
            cand[("vence_pronto", f"{t['tarea_id']}:{t['fecha']}")] = (
                f"«{t['tarea']}» ({t['materia']}) vence el {_fecha_humana(t['fecha'])} y no está entregada.")
    for t in grupos["vencidas_sin_entregar"]:
        if t["fecha"] and t["fecha"] >= corte:
            cand[("vencida", f"{t['tarea_id']}:{t['fecha']}")] = (
                f"«{t['tarea']}» ({t['materia']}) venció el {_fecha_humana(t['fecha'])} y Moodle la indica sin entregar.")
    for plataforma, de in _DE.items():
        ultima = conn.execute("SELECT estado FROM sesion_extraccion WHERE estudiante_id = ? AND plataforma = ? "
                              "ORDER BY id DESC LIMIT 1", (est, plataforma)).fetchone()
        if ultima and ultima["estado"] == "fallida":
            # una alerta por racha de fallos: se repite solo si antes hubo una lectura buena
            buena = conn.execute("SELECT MAX(fecha) AS f FROM sesion_extraccion WHERE estudiante_id = ? "
                                 "AND plataforma = ? AND estado = 'exitosa'", (est, plataforma)).fetchone()["f"]
            cand[("lectura_fallida", f"{plataforma}:{buena or 'nunca'}")] = (
                f"La última lectura {de} falló: lo que ves puede estar desactualizado.")
    minimo = prefs["asistencia_minima"]
    if minimo is not None:
        for f in conn.execute("""
            SELECT m.id, m.nombre, mat.asistencia FROM matricula mat JOIN materia m ON m.id = mat.materia_id
            WHERE mat.estudiante_id = ? AND mat.estado = 'en_curso' AND mat.asistencia IS NOT NULL
              AND mat.asistencia < ?""", (est, minimo)):
            a = f"{f['asistencia']:.0f}" if float(f["asistencia"]).is_integer() else f"{f['asistencia']:.1f}".replace(".", ",")
            cand[("asistencia_baja", f"{f['id']}:{f['asistencia']}")] = (
                f"Tu asistencia en {f['nombre']} es {a}%, por debajo del mínimo que indicaste ({minimo:.0f}%).")
    return cand


def generar(conn, estudiante_id: int, ahora: str = None) -> int:
    """Crea las alertas que falten y retira las no vistas que ya no aplican. Devuelve cuantas creo. Idempotente."""
    ahora = ahora or ahora_iso("seconds")
    prefs = P.obtener(conn, estudiante_id)
    activas = set(prefs["alertas_tipos"])
    cand = {k: v for k, v in _candidatas(conn, estudiante_id, ahora, prefs).items() if k[0] in activas}
    creadas = 0
    for (tipo, clave), mensaje in cand.items():
        cur = conn.execute("INSERT OR IGNORE INTO alerta (estudiante_id, tipo, clave, mensaje, creada_en) VALUES (?, ?, ?, ?, ?)",
                           (estudiante_id, tipo, clave, mensaje, ahora))
        creadas += cur.rowcount
    for f in conn.execute("SELECT id, tipo, clave FROM alerta WHERE estudiante_id = ? AND vista_en IS NULL",
                          (estudiante_id,)).fetchall():
        if (f["tipo"], f["clave"]) not in cand:
            conn.execute("DELETE FROM alerta WHERE id = ?", (f["id"],))
    antiguo = (iso_a_datetime(ahora) - timedelta(days=CONSERVAR_VISTAS_DIAS)).replace(tzinfo=None).isoformat(timespec="seconds")
    conn.execute("DELETE FROM alerta WHERE estudiante_id = ? AND vista_en IS NOT NULL AND vista_en < ?", (estudiante_id, antiguo))
    conn.commit()
    return creadas


def activas(conn, estudiante_id: int) -> list:
    return [{"id": f["id"], "tipo": f["tipo"], "mensaje": f["mensaje"], "creada_en": f["creada_en"]}
            for f in conn.execute("SELECT id, tipo, mensaje, creada_en FROM alerta WHERE estudiante_id = ? "
                                  "AND vista_en IS NULL ORDER BY creada_en DESC, id DESC LIMIT ?",
                                  (estudiante_id, LIMITE_LISTA))]


def marcar_vistas(conn, estudiante_id: int, ids=None, ahora: str = None) -> int:
    """Descarta alertas (todas, o solo las de `ids`). Una alerta descartada no vuelve a crearse."""
    ahora = ahora or ahora_iso("seconds")
    if ids is None:
        cur = conn.execute("UPDATE alerta SET vista_en = ? WHERE estudiante_id = ? AND vista_en IS NULL", (ahora, estudiante_id))
    else:
        ids = [int(i) for i in ids]
        if not ids:
            return 0
        marcas = ",".join("?" * len(ids))
        cur = conn.execute(f"UPDATE alerta SET vista_en = ? WHERE estudiante_id = ? AND vista_en IS NULL AND id IN ({marcas})",
                           [ahora, estudiante_id] + ids)
    conn.commit()
    return cur.rowcount
