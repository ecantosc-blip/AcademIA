"""
Script de diagnostico: imprime SOLO el texto 'display' de cada materia
(el mismo tipo de texto que ya se ve en pantalla en 'Mis Materias'), para
ajustar la extraccion del nombre real en cuentas con formato distinto
(ej. nivelacion/admision). No imprime tokens, cedula, ni otros datos.

Uso:
    python core/debug_materias_display.py
"""
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from sga.client import build_client_from_env


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()

    client = build_client_from_env()
    if not client.access_token:
        print("Falta SGA_ACCESS_TOKEN en .env")
        sys.exit(1)

    raw = client.get_materias()
    materias = raw.get("data", {}).get("eMateriasAsignadas", [])

    print(f"Total de materias: {len(materias)}\n")
    for i, m in enumerate(materias):
        print(f"--- Materia {i+1} ---")
        print(m.get("display", "(sin texto)"))
        print()
