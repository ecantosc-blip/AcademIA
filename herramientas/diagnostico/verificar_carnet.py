"""
Verificacion con cuenta real: ¿por que AcademIA dice "No hay un carne generado" si el ERP
si lo muestra en su propia pagina (seccion "Mi carne estudiantil")?

Hipotesis a revisar:
  1. El campo que usamos (data.eCarnet.pdf) ya no existe o cambio de nombre/forma en la
     respuesta real del endpoint GET /api/1.0/jwt/alumno/carnet.
  2. El token esta activo en un perfil (ej. nivelacion) que no es el que tiene el carne
     generado (ej. la carrera de grado), y el endpoint responde distinto segun el perfil activo.

Que imprime (y que NO):
  * Los NOMBRES de los campos de la respuesta (rutas como data.eCarnet.xxx) y su tipo.
  * Si alguno de esos nombres parece un enlace (campo que contenga "pdf", "url", "archivo",
    "link", "carne", "png", "jpg"), se imprime el nombre del campo y SOLO el dominio del
    valor (nunca la ruta completa ni el token que pueda llevar en la URL).
  * NUNCA nombres de personas, cedulas, ni el token. El volcado completo (sin el token) se
    guarda en diagnostico_local/ (ignorado por git) para revisarlo con mas calma si hace falta.

Uso (entorno virtual activo, desde la carpeta del proyecto):
    python herramientas/diagnostico/verificar_carnet.py
Te pide el token del ERP sin mostrarlo (copialo de F12 > Network > Authorization, sin 'Bearer').
"""
import re
from urllib.parse import urlparse

import comun
from comun import imprimir, depurar, guardar_local, token_erp
from sga.client import SGAClient

PATRON_ENLACE = re.compile(r"pdf|url|archivo|link|carne|png|jpg|imagen|anverso|reverso", re.I)


def recorrer(obj, ruta="", salida=None):
    salida = [] if salida is None else salida
    if isinstance(obj, dict):
        for k, v in obj.items():
            recorrer(v, f"{ruta}.{k}" if ruta else k, salida)
    elif isinstance(obj, list):
        for v in obj[:1]:  # una sola muestra: alcanza para ver la forma, no hace falta mas
            recorrer(v, f"{ruta}[]", salida)
    else:
        salida.append((ruta, obj, type(obj).__name__))
    return salida


def main():
    token = token_erp()
    if not token:
        imprimir("Falta el token. Nada que revisar."); return

    sga = SGAClient(base_url="https://sga.unemi.edu.ec", access_token=token)
    imprimir(f"Perfil activo del token: id={sga.get_perfil_principal_id()}")
    for p in sga.get_perfiles():
        imprimir(f"  perfil disponible: id={p.get('id')} clasificacion={p.get('clasificacion')} "
                 f"(1=Grado, el resto suele ser nivelacion/propedeutico)")

    try:
        bruto = sga.get_carnet()
    except Exception as e:
        imprimir(f"La peticion fallo: {depurar(e)}"); return

    filas = recorrer(bruto)
    imprimir(f"\n{len(filas)} campos en la respuesta. Nombre de campo : tipo (valor solo si parece un enlace):")
    for ruta, valor, tipo in filas:
        pista = ""
        if PATRON_ENLACE.search(ruta) and isinstance(valor, str) and valor:
            try:
                dominio = urlparse(valor if valor.startswith("http") else "https://x" + valor).hostname or "(ruta relativa)"
            except Exception:
                dominio = "(no es una URL)"
            pista = f"  -> dominio: {dominio}, largo: {len(valor)}"
        imprimir(f"  {ruta} : {tipo}{pista}")

    guardar_local("carnet_estructura.txt", "\n".join(f"{r} : {t}" for r, _, t in filas))
    imprimir("\nListo. El detalle completo (sin el token) quedo en diagnostico_local/carnet_estructura.txt")


if __name__ == "__main__":
    main()
