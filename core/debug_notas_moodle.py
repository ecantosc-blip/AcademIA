"""
Diagnostico de calificaciones de Moodle.

Muestra, para cada curso, EXACTAMENTE lo que devuelve la API de Moodle sobre
tus notas (tareas, categorias y totales), para entender por que el asistente
no ve ciertas notas o como obtener los totales N1, N2...

Uso (desde la carpeta del proyecto, con el entorno virtual activo):
    python core/debug_notas_moodle.py

Te pide el token de Moodle (puedes pegar el enlace moodlemobile://token=...
completo). El token NO se imprime. Lo que se imprime son solo tus notas, asi
que puedes copiar la salida y compartirla sin riesgo para tu cuenta.
"""
import os
import re
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
from moodle.client import MoodleClient, extraer_token_moodle


def _texto(html) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", str(html or ""))).strip()


def main(cliente: MoodleClient = None):
    load_dotenv()
    if cliente is None:
        token = extraer_token_moodle(input("Pega tu token de Moodle (o el enlace completo): "))
        cliente = MoodleClient(
            base_url=os.getenv("MOODLE_BASE_URL", "https://aulagradoa.unemi.edu.ec"), token=token
        )

    userid = cliente.get_site_info()["userid"]
    for curso in cliente.get_courses():
        print("\n" + "=" * 78)
        print("CURSO:", curso["fullname"], f"(id {curso['id']})")

        # 1) Lo que usa hoy el asistente: gradereport_user_get_grade_items
        print("\n[A] gradereport_user_get_grade_items  (tipo | id | categoria | nombre | nota | %)")
        try:
            data = cliente.get_grades(curso["id"], userid)
            for ug in data.get("usergrades", []):
                for it in ug.get("gradeitems", []):
                    print("  ", it.get("itemtype"), "|", it.get("id"), "|", it.get("categoryid"), "|",
                          repr(it.get("itemname")), "|", it.get("gradeformatted"), "|",
                          it.get("percentageformatted"))
        except Exception as e:
            print("   ERROR:", e)

        # 2) Alternativa: la tabla del "Informe del usuario" (la que ves en Moodle,
        #    con los nombres N1, N2, "Total N1"...)
        print("\n[B] gradereport_user_get_grades_table  (filas del informe, como en la pantalla)")
        try:
            tabla = cliente.call("gradereport_user_get_grades_table", courseid=curso["id"], userid=userid)
            filas = (tabla.get("tables") or [{}])[0].get("tabledata", [])
            if not filas:
                print("   (sin filas)")
            for fila in filas:
                if not isinstance(fila, dict):
                    continue
                celdas = [f"{k}={_texto(v.get('content'))}" for k, v in fila.items()
                          if isinstance(v, dict) and _texto(v.get("content"))]
                if celdas:
                    print("  ", " | ".join(celdas))
        except Exception as e:
            print("   No disponible en tu Moodle:", e)


if __name__ == "__main__":
    main()
