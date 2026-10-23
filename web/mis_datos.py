"""
Panel "Mis datos": transparencia y control para el estudiante.

- resumen(): que guarda AcademIA de la persona (cantidades por tipo, sin contenido).
- exportar(): todo lo suyo en un JSON descargable.
- borrar(): elimina todo lo suyo (el catalogo compartido de materias/docentes se queda:
  no es un dato personal). Las claves foraneas con ON DELETE CASCADE hacen el resto.

Respeta la proteccion de datos personales: el estudiante ve, se lleva y borra lo que
se guardo sobre el.
"""
import sqlite3

from db.setup import get_connection
from core.tiempo import ahora_iso

# (etiqueta que ve el estudiante, SQL de conteo). El parametro es el id del estudiante.
_CONTEO = [
    ("Materias que cursas o cursaste", "SELECT COUNT(*) FROM matricula WHERE estudiante_id = ?"),
    ("Notas por componente", "SELECT COUNT(*) FROM componente_nota c JOIN matricula m ON m.id = c.matricula_id WHERE m.estudiante_id = ?"),
    ("Eventos del calendario", "SELECT COUNT(*) FROM evento WHERE estudiante_id = ?"),
    ("Entregas registradas", "SELECT COUNT(*) FROM entrega WHERE estudiante_id = ?"),
    ("Prácticas de estudio", "SELECT COUNT(*) FROM practica WHERE estudiante_id = ?"),
    ("Lecturas de Moodle y ERP", "SELECT COUNT(*) FROM sesion_extraccion WHERE estudiante_id = ?"),
]


def resumen(estudiante_id: int) -> dict:
    conn = get_connection()
    try:
        conteos = [{"nombre": nombre, "cantidad": conn.execute(sql, (estudiante_id,)).fetchone()[0]}
                   for nombre, sql in _CONTEO]
        est = conn.execute("SELECT fecha_sincronizacion FROM estudiante WHERE id = ?", (estudiante_id,)).fetchone()
    finally:
        conn.close()
    return {"conteos": conteos, "ultima_sincronizacion": est["fecha_sincronizacion"] if est else None}


def _filas(conn, sql, params=()):
    return [dict(r) for r in conn.execute(sql, params)]


def exportar(estudiante_id: int) -> dict:
    conn = get_connection()
    try:
        est = _filas(conn, "SELECT id, nombre, sga_alumno_id, moodle_user_id, fecha_sincronizacion FROM estudiante WHERE id = ?", (estudiante_id,))
        matriculas = _filas(conn, """
            SELECT mat.id, m.nombre AS materia, m.semestre, m.creditos, mat.periodo, mat.estado, mat.nota_final, mat.asistencia
            FROM matricula mat JOIN materia m ON m.id = mat.materia_id WHERE mat.estudiante_id = ? ORDER BY mat.id""", (estudiante_id,))
        for mt in matriculas:
            mt["componentes"] = _filas(conn, "SELECT nombre_item, calificacion, porcentaje FROM componente_nota WHERE matricula_id = ? ORDER BY id", (mt["id"],))
        datos = {
            "exportado": ahora_iso("seconds"),
            "aviso": "Copia de los datos que AcademIA guarda sobre ti. No incluye contraseñas ni tokens: nunca se guardan.",
            "estudiante": est[0] if est else None,
            "matriculas": matriculas,
            "tareas_y_entregas": _filas(conn, """
                SELECT m.nombre AS materia, t.nombre AS tarea, t.fecha_entrega, COALESCE(e.entregada, 0) AS entregada
                FROM tarea t JOIN materia m ON m.id = t.materia_id
                JOIN matricula mat ON mat.materia_id = m.id AND mat.estudiante_id = ?
                LEFT JOIN entrega e ON e.tarea_id = t.id AND e.estudiante_id = mat.estudiante_id
                ORDER BY t.fecha_entrega""", (estudiante_id,)),
            "eventos": _filas(conn, "SELECT nombre, tipo, fecha FROM evento WHERE estudiante_id = ? ORDER BY fecha", (estudiante_id,)),
            "practicas": _filas(conn, """
                SELECT m.nombre AS materia, p.tema, p.fecha, p.total, p.aciertos
                FROM practica p JOIN materia m ON m.id = p.materia_id WHERE p.estudiante_id = ? ORDER BY p.fecha""", (estudiante_id,)),
            "lecturas": _filas(conn, "SELECT plataforma, fecha, estado, detalle FROM sesion_extraccion WHERE estudiante_id = ? ORDER BY id", (estudiante_id,)),
        }
    finally:
        conn.close()
    return datos


def borrar(estudiante_id: int) -> None:
    """Borra al estudiante; sus matriculas, notas, entregas, eventos, practicas, lecturas y
    sesiones se van en cascada. Las materias y los docentes (catalogo compartido) se quedan."""
    conn = get_connection()
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        with conn:
            conn.execute("DELETE FROM estudiante WHERE id = ?", (estudiante_id,))
    finally:
        conn.close()
