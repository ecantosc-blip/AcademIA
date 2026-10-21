"""
Sincroniza datos desde Moodle (Aulas Virtuales) hacia la base de datos unificada.
Este es el primer conector a implementar porque ya confirmamos que funciona
sin fricción (sin deteccion de bots).
"""
import os
import sys
from datetime import datetime

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from moodle.client import build_client_from_env
from db.setup import get_connection


def sync(estudiante_id: int = 1):
    from dotenv import load_dotenv
    load_dotenv()

    client = build_client_from_env()
    username = os.getenv("MOODLE_USERNAME")
    password = os.getenv("MOODLE_PASSWORD")

    conn = get_connection()
    cursor = conn.cursor()

    try:
        client.login(username, password, os.getenv("MOODLE_SERVICE", "moodle_mobile_app"))
        info = client.get_site_info()

        # 1. Guardar/actualizar estudiante
        cursor.execute("""
            INSERT INTO estudiante (id, nombre, moodle_user_id, fecha_sincronizacion)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                nombre = excluded.nombre,
                moodle_user_id = excluded.moodle_user_id,
                fecha_sincronizacion = excluded.fecha_sincronizacion
        """, (estudiante_id, info["fullname"], info["userid"], datetime.now().isoformat()))

        # 2. Sincronizar materias (cursos)
        courses = client.get_courses()
        course_ids = []
        for c in courses:
            course_ids.append(c["id"])
            cursor.execute("""
                INSERT INTO materia (moodle_course_id, nombre)
                VALUES (?, ?)
                ON CONFLICT(moodle_course_id) DO UPDATE SET nombre = excluded.nombre
            """, (c["id"], c["fullname"]))

            # asegurar la matricula
            cursor.execute("SELECT id FROM materia WHERE moodle_course_id = ?", (c["id"],))
            materia_id = cursor.fetchone()["id"]
            cursor.execute("""
                SELECT id FROM matricula WHERE estudiante_id = ? AND materia_id = ?
            """, (estudiante_id, materia_id))
            if not cursor.fetchone():
                cursor.execute("""
                    INSERT INTO matricula (estudiante_id, materia_id, estado)
                    VALUES (?, ?, 'en_curso')
                """, (estudiante_id, materia_id))

        # 3. Sincronizar tareas
        assignments_data = client.get_assignments(course_ids)
        for course in assignments_data.get("courses", []):
            cursor.execute("SELECT id FROM materia WHERE moodle_course_id = ?", (course["id"],))
            row = cursor.fetchone()
            if not row:
                continue
            materia_id = row["id"]

            for assign in course.get("assignments", []):
                due = assign.get("duedate")
                fecha_entrega = datetime.fromtimestamp(due).isoformat() if due else None
                cursor.execute("""
                    INSERT INTO tarea (materia_id, moodle_assign_id, nombre, fecha_entrega)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT DO NOTHING
                """, (materia_id, assign["id"], assign["name"], fecha_entrega))

        # 4. Sincronizar eventos proximos
        events = client.get_upcoming_events()
        for e in events.get("events", []):
            fecha = datetime.fromtimestamp(e["timestart"]).isoformat()
            cursor.execute("""
                INSERT INTO evento (nombre, tipo, fecha)
                VALUES (?, ?, ?)
            """, (e["name"], e.get("eventtype", "otro"), fecha))

        conn.commit()

        cursor.execute("""
            INSERT INTO sesion_extraccion (estudiante_id, plataforma, fecha, estado, detalle)
            VALUES (?, 'moodle', ?, 'exitosa', ?)
        """, (estudiante_id, datetime.now().isoformat(), f"{len(courses)} cursos, {len(events.get('events', []))} eventos"))
        conn.commit()

        print(f"Sincronizacion exitosa: {len(courses)} materias, eventos actualizados.")

    except Exception as e:
        cursor.execute("""
            INSERT INTO sesion_extraccion (estudiante_id, plataforma, fecha, estado, detalle)
            VALUES (?, 'moodle', ?, 'fallida', ?)
        """, (estudiante_id, datetime.now().isoformat(), str(e)))
        conn.commit()
        print(f"Error en sincronizacion: {e}")
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    sync()
