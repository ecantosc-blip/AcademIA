"""
Lectura segura de numeros tal como los entrega Moodle ("10,00", "80,00 %", "0–100", "-").

Reglas:
  * Nunca inventa: si el texto no es un numero claro devuelve None (no 0).
  * "-" o vacio significa "aun sin calificar" -> None.
  * La coma es decimal ("10,00" -> 10.0); si hay punto Y coma, el ultimo es el decimal
    ("1.234,56" -> 1234.56). Un solo punto se toma como decimal ("10.00" -> 10.0).
  * Un % se ignora al convertir; usa es_porcentaje() para saber si lo traia.
Nota: este modulo todavia no se usa en ningun calculo. Primero hay que verificar con
datos reales que significa cada campo (ver docs/VERIFICACION_MOODLE.md).
"""
import re
import math

_VACIOS = {"", "-", "–", "—", "n/a", "na", "s/n", "sin calificar"}
_NUMERO = re.compile(r"^[+-]?\d+(?:[.,]\d+)*$")


def _limpio(valor) -> str:
    texto = "" if valor is None else str(valor)
    texto = re.sub(r"<[^>]+>", " ", texto)               # restos de HTML
    texto = texto.replace(" ", " ").replace("−", "-")   # espacio duro y menos tipografico
    return re.sub(r"\s+", " ", texto).strip()


def es_porcentaje(valor) -> bool:
    return "%" in _limpio(valor)


def a_numero(valor):
    """float o None. No lanza excepciones."""
    if isinstance(valor, bool):
        return None
    if isinstance(valor, (int, float)):
        return float(valor) if math.isfinite(valor) else None
    texto = _limpio(valor).replace("%", "").replace(" ", "")
    if texto.lower() in _VACIOS or not _NUMERO.match(texto):
        return None
    signo = -1.0 if texto.startswith("-") else 1.0
    texto = texto.lstrip("+-")
    if "," in texto and "." in texto:
        decimal = "," if texto.rfind(",") > texto.rfind(".") else "."
        miles = "." if decimal == "," else ","
        texto = texto.replace(miles, "").replace(decimal, ".")
    elif texto.count(",") > 1:
        return None                                       # "1,234,567": ambiguo, no se adivina
    elif texto.count(".") > 1:
        if not re.match(r"^\d{1,3}(\.\d{3})+$", texto):
            return None
        texto = texto.replace(".", "")                    # "1.234.567" -> miles
    else:
        texto = texto.replace(",", ".")
    try:
        resultado = signo * float(texto)
    except ValueError:
        return None
    return resultado if math.isfinite(resultado) else None


def a_rango(valor):
    """'0–100' / '0-100' / '0 - 10,5' -> (0.0, 100.0); None si no es un rango claro."""
    texto = _limpio(valor).replace("–", "-").replace("—", "-")
    m = re.match(r"^\s*([+-]?[\d.,]+)\s*-\s*([+-]?[\d.,]+)\s*$", texto)
    if not m:
        return None
    minimo, maximo = a_numero(m.group(1)), a_numero(m.group(2))
    if minimo is None or maximo is None or maximo <= minimo:
        return None
    return minimo, maximo
