"""
Diagnostico del endpoint de horario del ERP: imprime SOLO la forma de los datos
(nombres de campos y tipos), nunca valores. El volcado crudo se guarda en
diagnostico_local/ (ignorado por Git) por si quieres revisarlo tu mismo.

Uso (desde la carpeta del proyecto):
    python herramientas/diagnostico/debug_horario.py
"""
import sys

import comun
from comun import imprimir, guardar_local, resumir_estructura
from sga.client import build_client_from_env


def main(client=None):
    from dotenv import load_dotenv
    load_dotenv()
    client = client or build_client_from_env()
    if not client.access_token:
        print("Falta SGA_ACCESS_TOKEN en .env")
        return 1
    try:
        data = client.get_horario()
        ruta = guardar_local("horario_raw.json", data)
        print(f"Volcado completo guardado SOLO en tu equipo: {ruta}")
        print("(carpeta ignorada por Git; no lo compartas: puede tener datos personales)\n")
        print("=== Estructura del JSON (sin valores) ===\n")
        for linea in resumir_estructura(data):
            imprimir(linea)
    except Exception as e:
        imprimir(f"Error: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
