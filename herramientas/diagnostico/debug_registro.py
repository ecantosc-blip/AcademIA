"""
Diagnostico del endpoint de "Registro Academico" (historial completo): solo la
forma de los datos.

Requiere SGA_ALUMNO_ID en el .env (la parte entre '/notas/' y la '/' final de la
URL que ves en DevTools al entrar a "Registro Academico" en el ERP).

Uso (desde la carpeta del proyecto):
    python herramientas/diagnostico/debug_registro.py
"""
import os
import sys

import comun
from comun import imprimir, guardar_local, resumir_estructura
from sga.client import build_client_from_env


def main(client=None, alumno_id=None):
    from dotenv import load_dotenv
    load_dotenv()
    client = client or build_client_from_env()
    alumno_id = alumno_id or os.getenv("SGA_ALUMNO_ID", "")
    if not client.access_token:
        print("Falta SGA_ACCESS_TOKEN en .env")
        return 1
    if not alumno_id:
        print("Falta SGA_ALUMNO_ID en .env - copialo de la URL que se ve en DevTools al "
              "entrar a 'Registro Academico' (la parte entre '/notas/' y la '/' final).")
        return 1
    try:
        data = client.get_registro_academico(alumno_id)
        ruta = guardar_local("registro_raw.json", data)
        print(f"Volcado completo guardado SOLO en tu equipo: {ruta}")
        print("(carpeta ignorada por Git; no lo compartas: puede tener datos personales)\n")
        print("=== Estructura del JSON (sin valores) ===\n")
        for linea in resumir_estructura(data, max_items_lista=2):
            imprimir(linea)
    except Exception as e:
        imprimir(f"Error: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
