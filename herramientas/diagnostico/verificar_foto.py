"""
Revisa por que no aparece tu foto de perfil de Moodle (solo se ve el circulo con iniciales).

Usa tu TOKEN de Moodle (MOODLE_TOKEN o te lo pide sin mostrarlo). NUNCA lo imprime ni imprime
la URL completa: solo el tipo de direccion y el resultado, asi que la salida se puede pegar en el chat.

Uso (desde la carpeta del proyecto, con el entorno virtual activo):
    python herramientas/diagnostico/verificar_foto.py
"""
import os
import sys
from urllib.parse import urlparse

import comun
from comun import imprimir, depurar, token_moodle
from core import foto
from moodle.client import MoodleClient


def main():
    base = os.getenv("MOODLE_BASE_URL", "https://aulagradoa.unemi.edu.ec")
    token = token_moodle()
    info = MoodleClient(base_url=base, token=token).get_site_info()
    url = info.get("userpictureurl") or ""
    imprimir(f"Moodle trae userpictureurl: {'si' if url else 'NO'}")
    if not url:
        return
    u = urlparse(url)
    imprimir(f"  esquema={u.scheme}  mismo_sitio={u.hostname == urlparse(base).hostname}")
    imprimir(f"  ruta empieza con: /{u.path.strip('/').split('/')[0]}/...  "
             f"(imagen generica de Moodle: {'/theme/image.php' in u.path})")
    valida = foto.url_valida(url, base)
    imprimir(f"La app la considera usable: {'si' if valida else 'NO (se muestran las iniciales)'}")
    if not valida:
        return
    datos, tipo, motivo = foto.descargar(valida, token)
    imprimir(f"Descarga: {motivo}" + (f"  ({tipo}, {len(datos)} bytes)" if datos else ""))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        imprimir(f"Error: {depurar(e)}")
        sys.exit(1)
