"""
Utilidades para que ni los registros, ni los mensajes de error, ni los
scripts de diagnostico expongan tokens o datos personales.

  * depurar(texto)         -> quita tokens, correos y numeros largos (cedulas)
  * enmascarar(texto)      -> quita ademas el nombre del alumno en textos del ERP
  * resumir_estructura(x)  -> describe la forma de un JSON sin mostrar valores
  * guardar_local(...)     -> guarda un volcado crudo SOLO en diagnostico_local/
                              (carpeta ignorada por Git)
  * Alias                  -> sustituye nombres reales por C1, C2, ... estables
"""
import os
import re
import json

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CARPETA_LOCAL = os.path.join(RAIZ, "diagnostico_local")

_PATRONES = [
    # JWT (access/refresh del ERP): tres bloques base64url separados por puntos
    (re.compile(r"eyJ[A-Za-z0-9_\-]{5,}\.[A-Za-z0-9_\-]{5,}\.[A-Za-z0-9_\-]*"), "[TOKEN]"),
    # wstoken=..., token=... (enlaces moodlemobile:// y URLs de la API de Moodle)
    (re.compile(r"(?i)((?:ws)?token=)[^\s&\"'<>]+"), r"\1[TOKEN]"),
    (re.compile(r"(?i)(password=)[^\s&\"'<>]+"), r"\1[OCULTO]"),
    (re.compile(r"(?i)(bearer\s+)[A-Za-z0-9_\-\.=]+"), r"\1[TOKEN]"),
    # tokens de Moodle (32 hex) y cadenas largas sin espacios
    (re.compile(r"\b[A-Fa-f0-9]{32,}\b"), "[TOKEN]"),
    (re.compile(r"\b[A-Za-z0-9_\-]{40,}\b"), "[SECRETO]"),
    (re.compile(r"[\w.+\-]+@[\w\-]+(?:\.[\w\-]+)+"), "[CORREO]"),
    (re.compile(r"\d{8,}"), "[#]"),
]


def depurar(texto) -> str:
    """Devuelve el texto sin tokens, contrasenas, correos ni numeros largos."""
    texto = "" if texto is None else str(texto)
    for patron, reemplazo in _PATRONES:
        texto = patron.sub(reemplazo, texto)
    return texto


def imprimir(*partes) -> None:
    """print() que pasa por depurar(): para todo lo que venga de un servicio externo."""
    print(depurar(" ".join(str(p) for p in partes)))


def enmascarar(texto: str) -> str:
    """Quita el nombre del alumno (todo antes de la primera coma) y los datos sensibles."""
    texto = texto or ""
    if ", " in texto:
        texto = "[NOMBRE] , " + texto.split(", ", 1)[1]
    return depurar(texto)


def resumir_estructura(obj, prefijo="", max_items_lista=1, profundidad=0, max_profundidad=6):
    """
    Recorre un JSON y devuelve lineas con su FORMA ('campo: tipo', 'lista de N
    elementos'). Nunca incluye valores reales.
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
                lineas.append(f"{ruta}: {type(valor).__name__}")
    elif isinstance(obj, list):
        lineas.append(f"{prefijo}: lista de {len(obj)} elemento(s)")
        for item in obj[:max_items_lista]:
            lineas.extend(resumir_estructura(item, f"{prefijo}[0]", max_items_lista, profundidad + 1, max_profundidad))
    else:
        lineas.append(f"{prefijo}: {type(obj).__name__}")
    return lineas


def guardar_local(nombre: str, datos, carpeta: str = None) -> str:
    """
    Guarda un volcado crudo (puede tener datos personales) SOLO en
    diagnostico_local/. La carpeta lleva su propio .gitignore ('*') ademas del
    del proyecto, y el archivo se crea con permisos de lectura solo para ti.
    """
    carpeta = carpeta or CARPETA_LOCAL
    os.makedirs(carpeta, exist_ok=True)
    marca = os.path.join(carpeta, ".gitignore")
    if not os.path.exists(marca):
        with open(marca, "w", encoding="utf-8") as f:
            f.write("*\n")
    nombre = re.sub(r"[^A-Za-z0-9_.-]", "_", nombre)
    ruta = os.path.join(carpeta, nombre)
    with open(ruta, "w", encoding="utf-8") as f:
        json.dump(datos, f, ensure_ascii=False, indent=2)
    try:
        os.chmod(ruta, 0o600)
    except OSError:
        pass
    return ruta


class Alias:
    """Asigna nombres neutros y estables: Alias('Curso')('Calculo') -> 'Curso 1'."""

    def __init__(self, etiqueta: str):
        self.etiqueta = etiqueta
        self._mapa = {}

    def __call__(self, valor) -> str:
        clave = str(valor)
        if clave not in self._mapa:
            self._mapa[clave] = f"{self.etiqueta} {len(self._mapa) + 1}"
        return self._mapa[clave]
