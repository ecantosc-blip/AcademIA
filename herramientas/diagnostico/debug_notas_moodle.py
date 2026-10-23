"""
Diagnostico de calificaciones de Moodle: muestra, por curso, lo que devuelve la
API sobre tus notas (tareas, categorias y totales), para entender por que el
asistente no ve ciertas notas o como obtener los totales N1, N2...

Usa tu TOKEN de Moodle (MOODLE_TOKEN o te lo pide sin mostrarlo). NUNCA se imprime.

PRIVACIDAD: los nombres de cursos y de items se sustituyen por "Curso 1", "Item 2"...
y los comentarios del docente se ocultan. Los NUMEROS (notas, maximo, porcentaje,
peso) si se muestran, porque son justo lo que se necesita para entender las
ponderaciones: son tus notas, revisa la salida antes de compartirla.

Uso (desde la carpeta del proyecto, con el entorno virtual activo):
    python herramientas/diagnostico/debug_notas_moodle.py
"""
import os
import re
import sys

import comun
from comun import imprimir, Alias, token_moodle
from moodle.client import MoodleClient

_CELDAS_OCULTAS = {"itemname", "feedback"}


def _texto(html) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", str(html or ""))).strip()


def main(cliente=None):
    from dotenv import load_dotenv
    load_dotenv()
    if cliente is None:
        cliente = MoodleClient(base_url=os.getenv("MOODLE_BASE_URL", "https://aulagradoa.unemi.edu.ec"),
                               token=token_moodle())
    alias_curso, alias_item = Alias("Curso"), Alias("Item")
    userid = cliente.get_site_info()["userid"]
    for curso in cliente.get_courses():
        print("\n" + "=" * 78)
        print(alias_curso(curso["id"]))

        print("\n[A] gradereport_user_get_grade_items  (tipo | id | categoria | nombre | nota | % | peso)")
        try:
            data = cliente.get_grades(curso["id"], userid)
            for ug in data.get("usergrades", []):
                for it in ug.get("gradeitems", []):
                    nombre = alias_item(f"{curso['id']}:{it.get('id')}") if it.get("itemname") else "(sin nombre)"
                    imprimir("  ", it.get("itemtype"), "|", it.get("id"), "|", it.get("categoryid"), "|", nombre, "|",
                             it.get("gradeformatted"), "|", it.get("percentageformatted"), "|",
                             it.get("weightformatted"))
        except Exception as e:
            imprimir("   ERROR:", e)

        print("\n[B] gradereport_user_get_grades_table  (filas del informe, como en la pantalla)")
        try:
            tabla = cliente.call("gradereport_user_get_grades_table", courseid=curso["id"], userid=userid)
            filas = (tabla.get("tables") or [{}])[0].get("tabledata", [])
            if not filas:
                print("   (sin filas)")
            for n, fila in enumerate(filas, 1):
                if not isinstance(fila, dict):
                    continue
                celdas = []
                for k, v in fila.items():
                    if not isinstance(v, dict) or not _texto(v.get("content")):
                        continue
                    celdas.append(f"{k}=[oculto]" if k in _CELDAS_OCULTAS else f"{k}={_texto(v.get('content'))}")
                if celdas:
                    imprimir("  ", f"fila {n}:", " | ".join(celdas))
        except Exception as e:
            imprimir("   No disponible en tu Moodle:", e)
    return 0


if __name__ == "__main__":
    sys.exit(main())
