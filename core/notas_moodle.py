"""
Que tan bien va el estudiante en cada materia, SEGUN MOODLE (no el ERP).

Se usa solo para ordenar prioridades en el plan de estudio, no para calcular notas oficiales.

  * Si Moodle informa el "Total del curso" con un porcentaje, ese es el dato.
  * Si no, se promedian los porcentajes de los totales por bloque ("Total N1", "Total N2"...).
  * Si no hay ninguno, la materia queda SIN DATO: no se inventa ni se supone una nota.

Limite conocido: no esta verificado con todos los cursos que "percentageformatted" signifique avance
(nota / maximo). Por eso el plan solo usa el ORDEN entre las materias de la misma persona, y si todas
salen iguales no reordena nada (ver prioridad()).
"""
import re

from core.numeros import a_numero

_TOTAL_CURSO = re.compile(r"^\s*(total del curso|course total)\s*$", re.I)
_TOTAL_BLOQUE = re.compile(r"^\s*total\s+n\s*\d", re.I)


def _pct(valor):
    n = a_numero(valor)
    return n if n is not None and 0 <= n <= 100 else None


def promedios(conn, estudiante_id: int) -> dict:
    """{materia_id: porcentaje (0-100) | None} para las materias con notas de Moodle sincronizadas."""
    filas = conn.execute("""
        SELECT mat.materia_id, cn.nombre_item, cn.porcentaje
        FROM componente_nota cn JOIN matricula mat ON mat.id = cn.matricula_id
        WHERE mat.estudiante_id = ?""", (estudiante_id,)).fetchall()
    total, bloques, vistas = {}, {}, set()
    for r in filas:
        mid, nombre, p = r["materia_id"], r["nombre_item"] or "", _pct(r["porcentaje"])
        vistas.add(mid)
        if p is None:
            continue
        if _TOTAL_CURSO.match(nombre):
            total[mid] = p
        elif _TOTAL_BLOQUE.match(nombre):
            bloques.setdefault(mid, []).append(p)
    out = {}
    for mid in vistas:
        if mid in total:
            out[mid] = total[mid]
        elif bloques.get(mid):
            out[mid] = sum(bloques[mid]) / len(bloques[mid])
        else:
            out[mid] = None
    return out


def prioridad(conn, estudiante_id: int) -> dict:
    """{materia_id: puesto} con 1 = la de menor porcentaje. Solo materias con dato.
    Si todas las materias con dato tienen el mismo valor (o hay menos de dos) no se prioriza nada."""
    datos = {m: p for m, p in promedios(conn, estudiante_id).items() if p is not None}
    if len(datos) < 2 or len(set(round(p, 1) for p in datos.values())) < 2:
        return {}
    orden = sorted(datos, key=lambda m: (datos[m], m))
    return {m: i + 1 for i, m in enumerate(orden)}
