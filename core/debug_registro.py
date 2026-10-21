"""
Script de diagnostico para mapear la estructura del endpoint de
"Registro Academico" (historial completo, no solo semestre actual),
SIN exponer datos personales. Mismo principio que debug_horario.py:
solo imprime nombres de campos y tipos, nunca valores reales.

Requiere SGA_ALUMNO_ID en el .env - copia ese valor directo de la URL
que ves en DevTools al entrar a "Registro Academico" en el SGA
(la parte entre '/notas/' y la '/' final).

Uso:
    python core/debug_registro.py
"""
import os
import sys
import json

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from sga.client import build_client_from_env
from core.debug_horario import resumir_estructura


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()

    client = build_client_from_env()
    alumno_id = os.getenv("SGA_ALUMNO_ID", "")

    if not client.access_token:
        print("Falta SGA_ACCESS_TOKEN en .env")
        sys.exit(1)
    if not alumno_id:
        print("Falta SGA_ALUMNO_ID en .env - copialo de la URL que se ve "
              "en DevTools al entrar a 'Registro Academico' en el SGA "
              "(la parte entre '/notas/' y la '/' final).")
        sys.exit(1)

    try:
        data = client.get_registro_academico(alumno_id)

        with open("registro_raw.json", "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print("JSON completo guardado localmente en: registro_raw.json")
        print("(ese archivo NO se comparte - es solo para tu referencia)\n")

        print("=== Estructura del JSON (sin datos reales, seguro de compartir) ===\n")
        for linea in resumir_estructura(data, max_items_lista=2):
            print(linea)

    except Exception as e:
        print(f"Error: {e}")
