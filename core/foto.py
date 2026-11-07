"""Descarga de la foto de perfil de Moodle (solo la del propio estudiante).

Devuelve (bytes, tipo, motivo). Si no se pudo, bytes es None y motivo explica por que en una
frase segura: nunca incluye la URL ni el token.
"""
from urllib.parse import urlparse, urljoin

import requests

MAX_BYTES = 2 * 1024 * 1024
TIPOS_OK = ("image/jpeg", "image/png", "image/gif", "image/webp")


def url_valida(url, base_url: str) -> str:
    """Devuelve la URL de la foto de perfil solo si es https y del mismo sitio de Moodle.

    Se guarda sin token. Que sea del mismo host evita que el servidor pida una dirección
    ajena (SSRF) o envíe el token de Moodle a otro sitio.
    """
    try:
        u, b = urlparse(url or ""), urlparse(base_url or "")
    except ValueError:
        return ""
    if u.scheme != "https" or not u.hostname or u.hostname != b.hostname:
        return ""
    if "/theme/image.php" in u.path:   # imagen genérica de Moodle: mejor mostrar las iniciales
        return ""
    return url


def _candidatas(url: str) -> list:
    """Moodle sirve las fotos por /webservice/pluginfile.php con token; si no, por /pluginfile.php."""
    ws = url.replace("/pluginfile.php/", "/webservice/pluginfile.php/", 1)
    return [ws, url] if ws != url else [url]


def _pedir(url: str, token: str, host: str):
    """Una petición, siguiendo hasta 2 redirecciones SOLO dentro del mismo sitio."""
    for _ in range(3):
        r = requests.get(url, params={"token": token}, timeout=8, allow_redirects=False, stream=True)
        if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("Location"):
            destino = urljoin(url, r.headers["Location"])
            p = urlparse(destino)
            if p.scheme != "https" or p.hostname != host:
                return None, "Moodle redirige a otro sitio"
            url = destino
            continue
        return r, None
    return None, "demasiadas redirecciones"


def descargar(url: str, token: str):
    host = urlparse(url).hostname
    motivo = "sin intentos"
    for cand in _candidatas(url):
        try:
            r, err = _pedir(cand, token, host)
        except requests.RequestException:
            motivo = "error de red al pedir la foto"
            continue
        if r is None:
            motivo = err
            continue
        tipo = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if r.status_code != 200:
            motivo = f"Moodle respondió {r.status_code}"
            continue
        if tipo not in TIPOS_OK:
            motivo = f"Moodle no devolvió una imagen (tipo: {tipo or 'vacío'})"
            continue
        datos = r.raw.read(MAX_BYTES + 1, decode_content=True)
        if len(datos) > MAX_BYTES:
            motivo = "la imagen es demasiado grande"
            continue
        return datos, tipo, "ok"
    return None, None, motivo


# ------------------------------------------------------------------ foto del ERP (SGA)
import re

_CLAVE_FOTO = re.compile(r"foto|avatar|imagen|image|picture|png|jpg", re.I)
_ES_IMAGEN = re.compile(r"\.(jpe?g|png|webp|gif)(\?|$)", re.I)


def buscar_en_sga(obj, base_url: str, _prof: int = 0) -> str:
    """Busca dentro de un JSON del ERP un valor que sea una foto: clave tipo foto/avatar/imagen
    y valor con extension de imagen. Devuelve la URL https del propio ERP o ''."""
    if _prof > 5:
        return ""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, str) and _CLAVE_FOTO.search(str(k)) and _ES_IMAGEN.search(v):
                u = urljoin(base_url.rstrip("/") + "/", v)
                if url_valida_erp(u, base_url):
                    return u
        for v in obj.values():
            r = buscar_en_sga(v, base_url, _prof + 1)
            if r:
                return r
    elif isinstance(obj, list):
        for v in obj[:20]:
            r = buscar_en_sga(v, base_url, _prof + 1)
            if r:
                return r
    return ""


def url_valida_erp(url, base_url: str) -> str:
    """Solo https y dentro de unemi.edu.ec (la foto del ERP puede estar en otro subdominio)."""
    try:
        u = urlparse(url or "")
    except ValueError:
        return ""
    h = u.hostname or ""
    if u.scheme != "https" or not (h == "unemi.edu.ec" or h.endswith(".unemi.edu.ec")):
        return ""
    return url


def descargar_erp(url: str, access_token: str, base_url: str):
    """Pide la foto al ERP. El token (Bearer) solo se envia al host del propio ERP."""
    if not url_valida_erp(url, base_url):
        return None, None, "dirección fuera de unemi.edu.ec"
    mismo = urlparse(url).hostname == urlparse(base_url).hostname
    try:
        r = requests.get(url, headers={"Authorization": f"Bearer {access_token}"} if mismo else {},
                         timeout=8, allow_redirects=False, stream=True)
    except requests.RequestException:
        return None, None, "error de red al pedir la foto"
    tipo = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
    if r.status_code != 200:
        return None, None, f"el ERP respondió {r.status_code}"
    if tipo not in TIPOS_OK:
        return None, None, f"el ERP no devolvió una imagen (tipo: {tipo or 'vacío'})"
    datos = r.raw.read(MAX_BYTES + 1, decode_content=True)
    if len(datos) > MAX_BYTES:
        return None, None, "la imagen es demasiado grande"
    return datos, tipo, "ok"
