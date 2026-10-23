"""
Verificacion con TU cuenta real de dos supuestos sobre Moodle (ver docs/VERIFICACION_MOODLE.md):

  1. Si el sitio permite consultar el estado de entrega (mod_assign_get_submission_status)
     y que valores devuelve.
  2. Que significa percentageformatted: avance (nota / maximo) o peso.

Usa tu TOKEN de Moodle (MOODLE_TOKEN, o te lo pide sin mostrarlo). No usa contrasena y
NUNCA lo imprime. La salida es corta y esta pensada para pegarla en el chat:
  * no contiene nombres de cursos, de tareas ni de items (se sustituyen por Curso 1, Item A...),
  * solo muestra NUMEROS de 3 items como maximo (nota, minimo, maximo, porcentaje, peso),
  * los errores se depuran (sin tokens ni URL).
Revisa la salida antes de compartirla.

Uso (desde la carpeta del proyecto, con el entorno virtual activo):
    python herramientas/diagnostico/verificar_moodle.py
"""
import os
import sys
from collections import Counter

import comun
from comun import imprimir, depurar, Alias, token_moodle
from core.numeros import a_numero, a_rango
from core.entregas import interpretar_estado
from moodle.client import MoodleClient

MAX_ITEMS_CON_NUMEROS = 3


def analizar_item(item: dict) -> dict:
    """Compara percentageformatted con nota/maximo. Devuelve solo numeros."""
    nota = a_numero(item.get("graderaw"))
    if nota is None:
        nota = a_numero(item.get("gradeformatted"))
    minimo = a_numero(item.get("grademin"))
    maximo = a_numero(item.get("grademax"))
    if maximo is None:
        rango = a_rango(item.get("rangeformatted"))
        if rango:
            minimo, maximo = rango
    pct = a_numero(item.get("percentageformatted"))
    peso = a_numero(item.get("weightformatted"))
    calculado = None
    if nota is not None and maximo is not None and maximo != (minimo or 0):
        calculado = round((nota - (minimo or 0)) / (maximo - (minimo or 0)) * 100, 2)
    coincide = None if (pct is None or calculado is None) else abs(pct - calculado) <= 0.6
    return {"nota": nota, "minimo": minimo, "maximo": maximo, "percentage": pct, "peso": peso,
            "percentage_calculado": calculado, "percentage_es_avance": coincide}


def _estado_entrega(cliente, userid, cursos, alias_curso):
    print("=== 1) ESTADO DE ENTREGA (mod_assign_get_submission_status) ===")
    asignaciones = cliente.get_assignments([c["id"] for c in cursos]).get("courses", [])
    cuenta, errores, probadas = Counter(), Counter(), 0
    for curso in asignaciones:
        tareas = [a for a in curso.get("assignments", []) if not a.get("nosubmissions")]
        if not tareas:
            print(f"{alias_curso(curso['id'])}: sin tareas con entrega en linea")
            continue
        probadas += 1
        try:
            resp = cliente.get_submission_status(tareas[0]["id"], userid)
        except Exception as e:
            errores[depurar(e)[:100]] += 1
            imprimir(f"{alias_curso(curso['id'])}: ERROR -> {e}")
            continue
        ultimo = resp.get("lastattempt") if isinstance(resp, dict) else None
        sub = ultimo.get("submission") if isinstance(ultimo, dict) else None
        status = sub.get("status") if isinstance(sub, dict) else None
        cuenta[status or "(sin submission)"] += 1
        claves = sorted(ultimo.keys())[:12] if isinstance(ultimo, dict) else None
        imprimir(f"{alias_curso(curso['id'])}: responde. status={status!r}  interpretado={interpretar_estado(resp)!r}  "
                 f"claves de lastattempt={claves}")
    print(f"\nResumen: cursos probados={probadas}, respuestas={sum(cuenta.values())}, errores={sum(errores.values())}")
    print(f"Valores de status: {dict(cuenta)}")
    if errores:
        imprimir("Errores distintos:", list(errores))
    print("Conclusion: " + (
        "LA FUNCION NO RESPONDE en este sitio/rol: las tareas seguiran 'Sin verificar'." if probadas and not cuenta else
        "la funcion responde; revisa que los status esten entre submitted/draft/new." if cuenta else
        "no hubo tareas con entrega en linea para probar."))


def _porcentaje(cliente, userid, cursos, alias_curso):
    print("\n=== 2) SIGNIFICADO DE percentageformatted ===")
    filas = []
    for curso in cursos:
        try:
            data = cliente.get_grades(curso["id"], userid)
        except Exception as e:
            imprimir(f"{alias_curso(curso['id'])}: ERROR -> {e}")
            continue
        for ug in data.get("usergrades", []):
            for it in ug.get("gradeitems", []):
                a = analizar_item(it)
                if a["nota"] is not None and a["percentage"] is not None and it.get("itemtype") != "course":
                    filas.append((curso["id"], it.get("itemtype"), a))
    if not filas:
        print("No encontre items con nota numerica para analizar.")
        return
    avance = sum(1 for *_, a in filas if a["percentage_es_avance"])
    comparables = sum(1 for *_, a in filas if a["percentage_es_avance"] is not None)
    print(f"Items analizados: {len(filas)}; comparables: {comparables}; "
          f"donde percentage = nota/maximo*100: {avance}")
    print("Conclusion: " + ("percentageformatted es AVANCE (nota sobre su maximo), NO el peso."
                            if comparables and avance == comparables else
                            "percentageformatted NO coincide siempre con nota/maximo: hay que revisarlo con el tutor/ERP."
                            if comparables else "no hay datos suficientes para concluir."))
    print(f"\nNumeros de hasta {MAX_ITEMS_CON_NUMEROS} items (nombres ocultos):")
    for n, (cid, tipo, a) in enumerate(filas[:MAX_ITEMS_CON_NUMEROS], 1):
        etiqueta = f"{alias_curso(cid)} / Item {n} ({tipo})"
        imprimir(f"  {etiqueta}: nota={a['nota']} min={a['minimo']} max={a['maximo']} "
                 f"percentage={a['percentage']} calculado={a['percentage_calculado']} peso={a['peso']}")


def main(cliente=None):
    from dotenv import load_dotenv
    load_dotenv()
    if cliente is None:
        cliente = MoodleClient(base_url=os.getenv("MOODLE_BASE_URL", "https://aulagradoa.unemi.edu.ec"),
                               token=token_moodle())
    try:
        userid = cliente.get_site_info()["userid"]
        cursos = cliente.get_courses()
    except Exception as e:
        imprimir(f"No pude conectar con Moodle: {e}")
        return 1
    alias_curso = Alias("Curso")
    print(f"Cursos: {len(cursos)}\n")
    _estado_entrega(cliente, userid, cursos, alias_curso)
    _porcentaje(cliente, userid, cursos, alias_curso)
    return 0


if __name__ == "__main__":
    sys.exit(main())
