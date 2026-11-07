"""
Revisa de donde puede salir tu foto de perfil y por que no aparece (solo se ve el circulo con iniciales).

La foto esta en el ERP (SGA). Este script mira SOLO NOMBRES de campos del ERP (token y carne) que
parezcan una foto, y prueba bajarla. NUNCA imprime tokens, URL completas ni valores personales:
muestra el nombre del campo, la extension, y la primera carpeta de la ruta (ej. /media/...).
La salida se puede pegar en el chat; revisala antes.

Uso (desde la carpeta del proyecto, con el entorno virtual activo):
    python herramientas/diagnostico/verificar_foto.py
Pide el token del ERP sin mostrarlo (Enter = usar SGA_ACCESS_TOKEN del .env).
"""
import os
import sys
from urllib.parse import urlparse

import comun
from comun import imprimir, depurar, token_erp
from core import foto
from sga.client import SGAClient, decodificar_jwt


def _candidatos(obj, ruta="", prof=0):
    """(ruta de claves, valor) de campos con pinta de foto/imagen, hasta 5 niveles."""
    if prof > 5:
        return
    if isinstance(obj, dict):
        for k, v in obj.items():
            r = f"{ruta}.{k}" if ruta else str(k)
            if isinstance(v, (dict, list)):
                yield from _candidatos(v, r, prof + 1)
            elif isinstance(v, str) and foto._CLAVE_FOTO.search(str(k)):
                yield r, v
    elif isinstance(obj, list):
        for v in obj[:5]:
            yield from _candidatos(v, ruta + "[]", prof + 1)


def _forma(valor: str, base: str) -> str:
    u = urlparse(valor)
    ext = os.path.splitext(u.path)[1].lower() or "sin extension"
    primera = "/" + u.path.strip("/").split("/")[0] + "/..." if u.path.strip("/") else "(vacia)"
    donde = "absoluta" if u.scheme else "relativa"
    mismo = f", host={'el del ERP' if u.hostname == urlparse(base).hostname else 'otro (' + (u.hostname or '') + ')'}" if u.scheme else ""
    return f"{donde}{mismo}, extension={ext}, empieza con {primera}, longitud={len(valor)}"


def main():
    base = os.getenv("SGA_BASE_URL", "https://sga.unemi.edu.ec")
    token = token_erp()
    cli = SGAClient(base, token, os.getenv("SGA_REFRESH_TOKEN") or None)

    imprimir("1) Campos del token del ERP que parecen foto:")
    payload = decodificar_jwt(token)
    hallados = list(_candidatos(payload))
    for ruta, v in hallados:
        imprimir(f"   {ruta}: {_forma(v, base)}")
    if not hallados:
        imprimir("   ninguno")

    imprimir("2) Campos del carne que parecen foto:")
    try:
        carnet = cli.get_carnet()
        h2 = list(_candidatos(carnet))
        for ruta, v in h2:
            imprimir(f"   {ruta}: {_forma(v, base)}")
        if not h2:
            imprimir("   ninguno")
    except Exception as e:
        imprimir(f"   no se pudo leer el carne: {depurar(e)}")

    url = foto.buscar_en_sga(payload, base)
    imprimir(f"3) La app encuentra una foto en el token: {'si' if url else 'NO'}")
    if url:
        datos, tipo, motivo = foto.descargar_erp(url, token, base)
        imprimir(f"   Descarga: {motivo}" + (f" ({tipo}, {len(datos)} bytes)" if datos else ""))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        imprimir(f"Error: {depurar(e)}")
        sys.exit(1)
