"""Preparacion comun de los scripts de diagnostico (ruta del proyecto y entrada del token)."""
import os
import sys
import getpass

RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if RAIZ not in sys.path:
    sys.path.insert(0, RAIZ)

from core.privacidad import imprimir, guardar_local, enmascarar, depurar, resumir_estructura, Alias  # noqa: E402,F401


def token_moodle() -> str:
    """Token de Moodle desde MOODLE_TOKEN o pedido sin mostrarlo en pantalla.
    Acepta el enlace completo moodlemobile://token=... (nunca se usa contrasena)."""
    from moodle.client import extraer_token_moodle
    crudo = os.getenv("MOODLE_TOKEN") or getpass.getpass(
        "Pega tu token de Moodle (o el enlace moodlemobile://...; no se muestra): ")
    return extraer_token_moodle(crudo)


def token_erp() -> str:
    """Token de acceso del ERP (JWT) pedido sin mostrarlo en pantalla; Enter = usar SGA_ACCESS_TOKEN del .env."""
    token = getpass.getpass("Pega el token del ERP (empieza por eyJ...; no se muestra): ").strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    return token or os.getenv("SGA_ACCESS_TOKEN") or ""
