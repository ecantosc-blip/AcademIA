"""Utilidades para comparar nombres de materias y leer niveles/semestres."""
import re
import unicodedata


def normalizar(texto: str) -> str:
    """Sin acentos y en minusculas, para comparar 'automatizacion' con 'automatización'."""
    if not texto:
        return ""
    return unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii").lower()


def coincide(a_norm: str, b_norm: str) -> bool:
    """
    True si dos nombres (ya normalizados) son iguales, o si uno esta dentro del
    otro COMO PALABRAS COMPLETAS. Asi 'programacion i' NO coincide con
    'programacion ii' (antes si, por ser una subcadena).
    """
    if not a_norm or not b_norm:
        return False
    if a_norm == b_norm:
        return True
    corto, largo = sorted((a_norm, b_norm), key=len)
    return re.search(rf"(?<![a-z0-9]){re.escape(corto)}(?![a-z0-9])", largo) is not None


_PALABRAS = {"primer": 1, "primero": 1, "segundo": 2, "tercer": 3, "tercero": 3, "cuarto": 4,
             "quinto": 5, "sexto": 6, "septimo": 7, "octavo": 8, "noveno": 9, "decimo": 10,
             "undecimo": 11}
_ROMANOS = {"i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6, "vii": 7, "viii": 8, "ix": 9, "x": 10}


def nivel_a_numero(texto):
    """'NIVEL 3', 'TERCER NIVEL', 'Nivel III' -> 3. None si no se reconoce."""
    n = normalizar(texto)
    if not n:
        return None
    m = re.search(r"\d+", n)
    if m:
        return int(m.group())
    palabras = re.findall(r"[a-z]+", n)
    for p in palabras:
        if p in _PALABRAS:
            return _PALABRAS[p]
    for p in palabras:
        if p in _ROMANOS:
            return _ROMANOS[p]
    return None
