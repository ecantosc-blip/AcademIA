"""
Script de diagnostico para mapear la estructura del endpoint de malla
curricular, SIN exponer datos personales. Mismo principio que
debug_horario.py y debug_registro.py: solo imprime nombres de campos
y tipos, nunca valores reales.

Uso:
    python core/debug_malla.py
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

    if not client.access_token:
        print("Falta SGA_ACCESS_TOKEN en .env")
        sys.exit(1)

    try:
        # Si la cuenta tiene varios perfiles (ej. Nivelacion + Grado), la
        # malla curricular probablemente solo existe/esta permitida para
        # el perfil de Grado. Se intenta cambiar automaticamente antes de
        # consultar, por si el token por defecto quedo en el perfil
        # equivocado.
        try:
            if client.cambiar_a_perfil_regular():
                print("Se detectaron multiples perfiles - cambiado al de tipo 'Grado' antes de consultar la malla.\n")
        except Exception as e:
            print(f"(no se pudo verificar perfiles: {e})\n")

        data = client.get_malla()

        with open("malla_raw.json", "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print("JSON completo guardado localmente en: malla_raw.json")
        print("(ese archivo NO se comparte - es solo para tu referencia)\n")

        print("=== Estructura del JSON (sin datos reales, seguro de compartir) ===\n")
        for linea in resumir_estructura(data, max_items_lista=2):
            print(linea)

    except Exception as e:
        print(f"Error: {e}")
