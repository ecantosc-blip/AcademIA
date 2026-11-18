"""
Datos reales para el tablero de la web: estado de las conexiones, materias
actuales, proximas fechas, horario, avance de la malla y bitacora de
extraccion. Todo sale de la base de datos del propio estudiante; no hay
cifras de ejemplo.
"""
import os
import sys
from datetime import datetime

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import tiempo, cambios, seguimiento, alertas
from core.tiempo import ahora_iso
from core.tareas import clasificar_tareas, en_ventana
from db.setup import get_connection
from sga.client import decodificar_jwt

ORDEN_DIAS = {"lunes": 1, "martes": 2, "miercoles": 3, "miércoles": 3, "jueves": 4,
              "viernes": 5, "sabado": 6, "sábado": 6, "domingo": 7}

# De donde sale cada dato que usa una herramienta del chat.
FUENTE_POR_HERRAMIENTA = {
    "tareas_pendientes": "Moodle",
    "tareas_por_materia": "Moodle",
    "calificaciones_detalladas": "Moodle",
    "rendimiento_materia": "Moodle",
    "profesor_materia": "Moodle",
    "resumen_semana": "Moodle",
    "calificacion_materia": "ERP",
    "comparar_materias": "ERP",
    "nota_necesaria": "ERP",
    "materias_por_semestre": "ERP",
    "materias_faltantes": "ERP",
    "materias_aprobadas": "ERP",
    "record_academico": "ERP",
    "notas_por_semestre": "ERP",
    "materias_actuales": "ERP",
    "rendimiento_actual": "Moodle",
    "horario_materia": "ERP",
}


def _hora(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).strftime("%H:%M")
    except Exception:
        return ""


def ultima_sincronizacion(conn, estudiante_id: int) -> dict:
    """Hora de la ultima extraccion exitosa por plataforma ('moodle' / 'sga')."""
    filas = conn.execute("""
        SELECT plataforma, MAX(fecha) AS fecha FROM sesion_extraccion
        WHERE estudiante_id = ? AND estado = 'exitosa' GROUP BY plataforma
    """, (estudiante_id,)).fetchall()
    return {f["plataforma"]: f["fecha"] for f in filas}


def fuentes_de(herramientas: list, estudiante_id: int) -> list:
    """Etiquetas de fuente para una respuesta del chat, ej. [{'fuente': 'ERP', 'hora': '13:22'}]."""
    nombres = []
    for h in herramientas or []:
        f = FUENTE_POR_HERRAMIENTA.get(h)
        if f and f not in nombres:
            nombres.append(f)
    if not nombres:
        return []
    conn = get_connection()
    try:
        ultimas = ultima_sincronizacion(conn, estudiante_id)
    finally:
        conn.close()
    clave = {"Moodle": "moodle", "ERP": "sga"}
    return [{"fuente": n, "hora": _hora(ultimas.get(clave[n], ""))} for n in nombres]


def construir_panel(sesion: dict) -> dict:
    est = sesion["estudiante_id"]
    ahora = ahora_iso("seconds")
    conn = get_connection()
    try:
        # ---- Conexiones ----
        exp_erp = None
        try:
            exp_erp = decodificar_jwt(sesion.get("sga_access_token", "")).get("exp")
        except Exception:
            pass
        ultimas = ultima_sincronizacion(conn, est)

        # ---- Materias actuales (con sus totales de Moodle) ----
        materias = []
        filas = conn.execute("""
            SELECT mat.id AS matricula_id, m.nombre, mat.nota_final, mat.asistencia
            FROM matricula mat JOIN materia m ON m.id = mat.materia_id
            WHERE mat.estudiante_id = ? AND mat.estado = 'en_curso'
            ORDER BY m.nombre
        """, (est,)).fetchall()
        for f in filas:
            comps = conn.execute("""
                SELECT nombre_item, calificacion FROM componente_nota
                WHERE matricula_id = ? AND nombre_item LIKE 'Total %'
                  AND nombre_item <> 'Total del curso'
                  AND TRIM(IFNULL(calificacion, '')) NOT IN ('', '-')
                ORDER BY id
            """, (f["matricula_id"],)).fetchall()
            materias.append({
                "nombre": f["nombre"],
                "nota_final": f["nota_final"],
                "asistencia": f["asistencia"],
                "totales": [{"nombre": c["nombre_item"].replace("Total ", ""), "valor": c["calificacion"]}
                            for c in comps],
            })

        # ---- Proximas fechas: tareas (pendientes y sin verificar) + eventos ----
        # Criterio unico en core/tareas.py. Las tareas "sin verificar" se listan, pero
        # marcadas como tales: no se afirma que esten pendientes ni entregadas.
        grupos = clasificar_tareas(conn, est, ahora=ahora)
        fechas = []
        for t in grupos["pendientes"] + [x for x in grupos["sin_verificar"] if not x["vencida"]]:
            if t["fecha"] is None:
                continue
            fechas.append({"tipo": "Tarea", "nombre": t["tarea"], "materia": t["materia"], "fecha": t["fecha"],
                           "estado": t["estado"] or "sin_verificar"})
        for r in conn.execute("""
            SELECT nombre, tipo, fecha FROM evento WHERE estudiante_id = ? AND fecha >= ?
        """, (est, ahora)):
            fechas.append({"tipo": "Evento", "nombre": r["nombre"], "materia": "", "fecha": r["fecha"], "estado": None})
        fechas.sort(key=lambda x: x["fecha"])
        fechas = fechas[:8]

        # ---- Horario semanal ----
        bloques = conn.execute("""
            SELECT DISTINCT m.nombre AS materia, h.dia, h.hora_inicio, h.hora_fin, h.aula
            FROM horario h
            JOIN materia m ON m.id = h.materia_id
            JOIN matricula mat ON mat.materia_id = m.id AND mat.estudiante_id = ?
        """, (est,)).fetchall()
        horario = {}
        for b in bloques:
            dia = (b["dia"] or "").strip()
            horario.setdefault(dia, []).append({
                "materia": b["materia"], "inicio": b["hora_inicio"], "fin": b["hora_fin"], "aula": b["aula"]})
        dias = sorted(horario, key=lambda d: ORDEN_DIAS.get(d.lower(), 9))
        horario = [{"dia": d, "clases": sorted(horario[d], key=lambda c: c["inicio"] or "")} for d in dias]

        # ---- Avance de la malla ----
        total = conn.execute("""
            SELECT COUNT(DISTINCT m.id) AS n FROM materia m
            JOIN matricula mat ON mat.materia_id = m.id AND mat.estudiante_id = ?
            WHERE m.semestre IS NOT NULL
        """, (est,)).fetchone()["n"]
        aprobadas = conn.execute("""
            SELECT COUNT(DISTINCT m.id) AS n FROM materia m
            WHERE m.semestre IS NOT NULL AND EXISTS (
                SELECT 1 FROM matricula mat
                WHERE mat.materia_id = m.id AND mat.estudiante_id = ? AND mat.estado = 'aprobada')
        """, (est,)).fetchone()["n"]

        # ---- Promedio por semestre (solo materias aprobadas con nota) ----
        semestres = [{"semestre": r["semestre"], "promedio": r["prom"], "materias": r["n"]}
                     for r in conn.execute("""
            SELECT m.semestre AS semestre, ROUND(AVG(mat.nota_final), 1) AS prom, COUNT(*) AS n
            FROM matricula mat JOIN materia m ON m.id = mat.materia_id
            WHERE mat.estudiante_id = ? AND mat.estado = 'aprobada'
              AND mat.nota_final IS NOT NULL AND m.semestre IS NOT NULL
            GROUP BY m.semestre ORDER BY m.semestre
        """, (est,))]

        # ---- Resumen para la pantalla de Inicio ----
        limite7 = ahora_iso("seconds", days=7)
        vencen = len(en_ventana(grupos["pendientes"], ahora, limite7))
        sin_verificar_7d = len(en_ventana(grupos["sin_verificar"], ahora, limite7))
        prom = conn.execute("""
            SELECT ROUND(AVG(nota_final), 1) AS p FROM matricula
            WHERE estudiante_id = ? AND estado = 'aprobada' AND nota_final IS NOT NULL
        """, (est,)).fetchone()["p"]
        inicio = {"vencen_7d": vencen, "sin_verificar_7d": sin_verificar_7d,
                  "vencidas_sin_entregar": len(grupos["vencidas_sin_entregar"]), "promedio_general": prom,
                  # Actividades que Moodle confirma como entregadas, para "Mi rendimiento académico".
                  # Es un conteo de hechos verificados (tabla entrega), no una medida de desempeño.
                  "entregadas_total": len(grupos["entregadas"])}

        # ---- Bitacora ----
        bitacora = [{"plataforma": "ERP" if r["plataforma"] == "sga" else "Moodle",
                     "fecha": r["fecha"], "estado": r["estado"], "detalle": r["detalle"] or ""}
                    for r in conn.execute("""
            SELECT plataforma, fecha, estado, detalle FROM sesion_extraccion
            WHERE estudiante_id = ? ORDER BY id DESC LIMIT 12
        """, (est,))]
        novedades_nuevas = cambios.contar_nuevas(conn, est)
        indicador = seguimiento.evaluar(conn, est)
        try:
            alertas.generar(conn, est)
            alertas_activas = alertas.activas(conn, est)
        except Exception:
            conn.rollback()
            alertas_activas = []              # las alertas nunca deben impedir que cargue el panel
    finally:
        conn.close()

    return {
        "nombre": sesion.get("nombre", ""),
        "novedades_nuevas": novedades_nuevas,
        "seguimiento": indicador,
        "alertas": alertas_activas,
        "conexiones": {
            "moodle": {"ultima": ultimas.get("moodle")},
            "erp": {"ultima": ultimas.get("sga"), "vence": exp_erp, "renovable": bool(sesion.get("sga_refresh_token"))},
        },
        "ahora": tiempo.ahora().timestamp(),
        "materias": materias,
        "fechas": fechas,
        "horario": horario,
        "malla": {"total": total, "aprobadas": aprobadas},
        "semestres": semestres,
        "inicio": inicio,
        "bitacora": bitacora,
    }
