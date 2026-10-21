"""
Script de diagnostico para mapear la estructura de calificaciones de Moodle
(gradereport_user_get_grade_items), SIN exponer datos personales.

Objetivo: confirmar como distingue Moodle entre una CATEGORIA (ej. "N1",
"EXP1") y una TAREA INDIVIDUAL dentro de esa categoria (ej. "S3-TALLER_1"),
para poder responder preguntas especificas como "cuanto llevo en N1"
en vez de solo dar una lista plana de todo mezclado.

Uso:
    python core/debug_calificaciones.py
"""
import os
import sys
import json

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from moodle.client import build_client_from_env
from core.debug_horario import resumir_estructura


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()

    client = build_client_from_env()
    username = os.getenv("MOODLE_USERNAME")
    password = os.getenv("MOODLE_PASSWORD")

    if not username or not password:
        print("Faltan MOODLE_USERNAME / MOODLE_PASSWORD en .env")
        sys.exit(1)

    try:
        client.login(username, password, os.getenv("MOODLE_SERVICE", "moodle_mobile_app"))
        info = client.get_site_info()
        userid = info["userid"]
        courses = client.get_courses()

        if not courses:
            print("No se encontraron cursos matriculados.")
            sys.exit(0)

        # Se usa el primer curso como muestra (misma estructura para todos)
        curso = courses[0]
        data = client.get_grades(curso["id"], userid)

        with open("calificaciones_raw.json", "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print("JSON completo guardado localmente en: calificaciones_raw.json")
        print("(ese archivo NO se comparte - puede tener datos identificables)\n")

        print(f"=== Estructura de calificaciones del curso: '{curso.get('fullname', '')[:30]}...' ===\n")
        print("(estructura y tipos de dato - seguro de compartir, sin valores reales)\n")
        for linea in resumir_estructura(data, max_items_lista=3):
            print(linea)

        # Ademas, se imprime SOLO los campos 'itemtype' e 'itemname' de cada
        # item (sin calificaciones reales), que es justo lo que necesitamos
        # para distinguir categorias de tareas individuales.
        print("\n=== Lista de itemtype + itemname (sin calificaciones) ===\n")
        for grade_item in data.get("usergrades", []):
            for item in grade_item.get("gradeitems", []):
                tipo = item.get("itemtype")
                nombre = item.get("itemname")
                modulo = item.get("itemmodule")
                print(f"itemtype={tipo!r:12} itemmodule={modulo!r:10} itemname={nombre!r}")

    except Exception as e:
        print(f"Error: {e}")
