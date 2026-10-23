"""
Diagnostico del endpoint de malla curricular del ERP: solo la forma de los datos.

Uso (desde la carpeta del proyecto):
    python herramientas/diagnostico/debug_malla.py
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
        # Con varios perfiles (Nivelacion + Grado) la malla solo existe para el de Grado.
        try:
            if client.cambiar_a_perfil_regular():
                print("Se detectaron multiples perfiles - cambiado al de tipo 'Grado'.\n")
        except Exception as e:
            imprimir(f"(no se pudo verificar perfiles: {e})\n")
        data = client.get_malla()
        ruta = guardar_local("malla_raw.json", data)
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
