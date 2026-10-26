"""
Verificacion con cuenta real: ¿el ERP (SGA) entrega los PESOS / maximos de cada componente de nota?

Contexto: Moodle UNEMI no entrega pesos ni maximos (ver docs/VERIFICACION_MOODLE.md). Antes de
construir el simulador de notas hay que saber si el ERP si los trae, en las dos consultas que
el proyecto ya usa: materias actuales y registro de notas. No se prueba ninguna otra ruta.

Que imprime (y que NO):
  * Nombres de campos (rutas como  eMateriasAsignadas[].materia.xxx) y su tipo.
  * Para campos con nombre de peso/porcentaje/maximo/minimo: el NUMERO (hasta 12, sin repetir).
  * NUNCA textos, nombres de materias o personas, cedulas ni el token.
  * El listado completo de campos (sin valores) se guarda en diagnostico_local/ (ignorado por git).

Uso (entorno virtual activo, desde la carpeta del proyecto):
    python herramientas/diagnostico/verificar_erp.py
Te pide el token del ERP sin mostrarlo (copialo de F12 > Network > Authorization, sin 'Bearer').
"""
import os
import re
import sys

import comun
from comun import imprimir, depurar, guardar_local, token_erp
from core.numeros import a_numero
from sga.client import SGAClient, segundos_para_vencer

# Campos que podrian indicar el peso o el maximo de un componente de nota.
PATRON_PESO = re.compile(r"peso|porcent|ponder|maxim|minim|valor_?max|puntaje", re.I)
# Campos relacionados con la evaluacion en general (solo se listan sus nombres).
PATRON_EVAL = re.compile(r"peso|porcent|ponder|maxim|minim|modelo|evalua|componente|parcial|aporte|"
                         r"calific|nota|examen|gestion|acd", re.I)
MAX_NUMEROS = 12
MAX_LINEAS = 80


def recorrer(obj, ruta="", salida=None):
    """Todas las rutas del JSON como (ruta, valor_escalar_o_None, tipo). Las listas se aplanan con []."""
    salida = [] if salida is None else salida
    if isinstance(obj, dict):
        for k, v in obj.items():
            recorrer(v, f"{ruta}.{k}" if ruta else str(k), salida)
    elif isinstance(obj, list):
        for item in obj:
            recorrer(item, ruta + "[]", salida)
        if not obj:
            salida.append((ruta + "[]", None, "lista vacia"))
    else:
        salida.append((ruta, obj, type(obj).__name__))
    return salida


def analizar(respuesta) -> dict:
    """Resumen sin datos personales: campos de evaluacion, numeros de peso y conteos."""
    hojas = recorrer(respuesta)
    campos = {}
    for ruta, _, tipo in hojas:
        campos.setdefault(ruta, tipo)
    evaluacion = sorted(r for r in campos if PATRON_EVAL.search(r.split(".")[-1]))
    numeros, vistos = [], set()
    for ruta, valor, _ in hojas:
        if not PATRON_PESO.search(ruta.split(".")[-1]) or isinstance(valor, bool):
            continue
        n = a_numero(valor)
        if n is not None and (ruta, n) not in vistos:
            vistos.add((ruta, n))
            numeros.append((ruta, n))
    return {"total_campos": len(campos), "campos": sorted(campos), "evaluacion": evaluacion,
            "numeros": numeros[:MAX_NUMEROS], "con_peso": bool(numeros)}


def _informe(titulo, respuesta):
    print(f"\n=== {titulo} ===")
    if not isinstance(respuesta, (dict, list)):
        print("La respuesta no tiene la forma esperada.")
        return None
    a = analizar(respuesta)
    print(f"Campos distintos en la respuesta: {a['total_campos']}")
    print(f"Campos relacionados con evaluacion/nota/peso ({len(a['evaluacion'])}; solo nombres):")
    for r in a["evaluacion"][:MAX_LINEAS]:
        print("  ", r)
    if len(a["evaluacion"]) > MAX_LINEAS:
        print(f"   ... y {len(a['evaluacion']) - MAX_LINEAS} mas (estan en el archivo local)")
    if a["numeros"]:
        print("Numeros en campos con nombre de peso/porcentaje/maximo/minimo:")
        for ruta, n in a["numeros"]:
            print(f"   {ruta} = {n}")
    else:
        print("Ningun campo con nombre de peso/porcentaje/maximo/minimo trae un numero.")
    return a


def main(cliente=None, token=None, carpeta_local=None):
    from dotenv import load_dotenv
    load_dotenv()
    if cliente is None:
        token = token if token is not None else token_erp()
        if not token:
            print("No hay token. Vuelve a correr el script y pega uno.")
            return 1
        resto = segundos_para_vencer(token)
        if resto is not None and resto <= 0:
            print("El token ya vencio. Copia uno nuevo y vuelve a correr esto.")
            return 1
        cliente = SGAClient(os.getenv("SGA_BASE_URL", "https://sgaestudiante.unemi.edu.ec"), token,
                            os.getenv("SGA_REFRESH_TOKEN") or None)
    try:
        cliente.cambiar_a_perfil_regular()
        materias = cliente.get_materias()
    except Exception as e:
        imprimir(f"No pude leer las materias del ERP: {e}")
        return 1
    informes = {"materias_actuales": _informe("1) MATERIAS ACTUALES (/alumno/materias)", materias)}

    registro = None
    try:
        alumno_id = cliente.get_alumno_id_automatico()
        if alumno_id:
            registro = cliente.get_registro_academico(alumno_id)
        else:
            print("\n=== 2) REGISTRO DE NOTAS ===\nNo se pudo detectar el id de alumno: se omite.")
    except Exception as e:
        imprimir(f"\n=== 2) REGISTRO DE NOTAS ===\nNo pude leerlo: {e}")
    if registro is not None:
        informes["registro_notas"] = _informe("2) REGISTRO DE NOTAS (/alumno/notas)", registro)

    hay = any(i and i["con_peso"] for i in informes.values())
    print("\n=== CONCLUSION ===")
    print("Hay campos numericos con nombre de peso/maximo: revisa arriba si corresponden a componentes de nota."
          if hay else
          "El ERP NO entrega pesos ni maximos por componente en estas dos consultas. "
          "Los pesos tendrian que venir del sílabo (declarados por el estudiante) o consultarse con el tutor.")
    try:
        ruta = guardar_local("erp_campos.json", {k: (v or {}).get("campos") for k, v in informes.items()},
                             carpeta_local)
        print(f"\nListado completo de campos (sin valores) guardado en: {ruta}")
    except Exception as e:
        imprimir(f"No se pudo guardar el listado local: {e}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        imprimir("Error:", depurar(e))
        sys.exit(1)
