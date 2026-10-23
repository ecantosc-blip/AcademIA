"""
Diagnostico de la estructura de calificaciones de Moodle
(gradereport_user_get_grade_items), para distinguir una CATEGORIA (N1, EXP1...)
de una TAREA individual. Usa tu TOKEN de Moodle (nunca tu contrasena).

Imprime la forma de los datos y, por cada item, solo su tipo y si tiene nombre
(no el texto del nombre ni las notas). El volcado crudo se guarda en
diagnostico_local/ (ignorado por Git).

Uso (desde la carpeta del proyecto):
    python herramientas/diagnostico/debug_calificaciones.py
"""
import os
import sys

import comun
from comun import imprimir, guardar_local, resumir_estructura, token_moodle
from moodle.client import MoodleClient, extraer_componente


def main(client=None):
    from dotenv import load_dotenv
    load_dotenv()
    if client is None:
        client = MoodleClient(base_url=os.getenv("MOODLE_BASE_URL", "https://aulagradoa.unemi.edu.ec"),
                              token=token_moodle())
    try:
        userid = client.get_site_info()["userid"]
        courses = client.get_courses()
        if not courses:
            print("No se encontraron cursos matriculados.")
            return 0
        curso = courses[0]   # una muestra: misma estructura en todos
        data = client.get_grades(curso["id"], userid)
        ruta = guardar_local("calificaciones_raw.json", data)
        print(f"Volcado completo guardado SOLO en tu equipo: {ruta}")
        print("(carpeta ignorada por Git; no lo compartas: tiene tus notas)\n")
        print("=== Estructura de calificaciones de un curso (sin valores) ===\n")
        for linea in resumir_estructura(data, max_items_lista=3):
            imprimir(linea)
        print("\n=== Items: tipo, modulo y si tienen nombre (sin textos ni notas) ===\n")
        for grade_item in data.get("usergrades", []):
            for item in grade_item.get("gradeitems", []):
                nombre = (item.get("itemname") or "").strip()
                etiqueta = extraer_componente(nombre) if nombre else None
                descripcion = "vacio" if not nombre else f"texto de {len(nombre)} caracteres"
                print(f"itemtype={item.get('itemtype')!r:12} itemmodule={item.get('itemmodule')!r:10} "
                      f"itemname={descripcion}  etiqueta_componente={etiqueta}")
    except Exception as e:
        imprimir(f"Error: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
