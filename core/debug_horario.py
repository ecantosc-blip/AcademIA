"""
Script de diagnostico para mapear la estructura del endpoint de horario,
SIN exponer datos personales. Imprime solo los NOMBRES de los campos
(claves del JSON) y el tipo de cada valor (texto, numero, lista, etc.),
nunca el contenido real.

Esto es seguro de compartir en el chat - no incluye nombres, cedulas,
horarios reales ni nada identificable, solo la "forma" de los datos.

Uso:
    python core/debug_horario.py
"""
import os
import sys
import json

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from sga.client import build_client_from_env


def resumir_estructura(obj, prefijo="", max_items_lista=1, profundidad=0, max_profundidad=6):
    """
    Recorre el JSON y devuelve una lista de lineas describiendo la forma
    de los datos: 'campo -> tipo' o 'campo -> lista de N elementos, cada
    uno con estos campos: [...]'. Nunca imprime valores reales.
    """
    lineas = []
    if profundidad > max_profundidad:
        return [f"{prefijo}: (profundidad maxima alcanzada)"]

    if isinstance(obj, dict):
        for clave, valor in obj.items():
            ruta = f"{prefijo}.{clave}" if prefijo else clave
            if isinstance(valor, (dict, list)):
                lineas.extend(resumir_estructura(valor, ruta, max_items_lista, profundidad + 1, max_profundidad))
            else:
                tipo = type(valor).__name__
                lineas.append(f"{ruta}: {tipo}")
    elif isinstance(obj, list):
        lineas.append(f"{prefijo}: lista de {len(obj)} elemento(s)")
        for item in obj[:max_items_lista]:
            lineas.extend(resumir_estructura(item, f"{prefijo}[0]", max_items_lista, profundidad + 1, max_profundidad))
    else:
        lineas.append(f"{prefijo}: {type(obj).__name__}")

    return lineas


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()

    client = build_client_from_env()

    if not client.access_token:
        print("Falta SGA_ACCESS_TOKEN en .env")
        sys.exit(1)

    try:
        data = client.get_horario()

        # Se guarda el JSON completo SOLO en tu computadora (nunca se
        # imprime aqui), por si luego necesitas revisarlo tu mismo.
        with open("horario_raw.json", "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print("JSON completo guardado localmente en: horario_raw.json")
        print("(ese archivo NO se comparte - es solo para tu referencia)\n")

        print("=== Estructura del JSON (sin datos reales, seguro de compartir) ===\n")
        lineas = resumir_estructura(data)
        for linea in lineas:
            print(linea)

    except Exception as e:
        print(f"Error: {e}")
