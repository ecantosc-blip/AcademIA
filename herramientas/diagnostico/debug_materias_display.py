"""
Diagnostico: imprime el texto 'display' de cada materia del ERP YA ENMASCARADO
(sin el nombre del alumno, sin cedula, sin tokens), para ajustar la extraccion
del nombre de la materia en cuentas con otro formato (nivelacion/admision).

Uso (desde la carpeta del proyecto):
    python herramientas/diagnostico/debug_materias_display.py
"""
import sys

import comun
from comun import imprimir, enmascarar
from sga.client import build_client_from_env


def main(client=None):
    from dotenv import load_dotenv
    load_dotenv()
    client = client or build_client_from_env()
    if not client.access_token:
        print("Falta SGA_ACCESS_TOKEN en .env")
        return 1
    raw = client.get_materias()
    materias = raw.get("data", {}).get("eMateriasAsignadas", [])
    print(f"Total de materias: {len(materias)}\n")
    for i, m in enumerate(materias):
        print(f"--- Materia {i+1} ---")
        imprimir(enmascarar(m.get("display", "(sin texto)")))
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
