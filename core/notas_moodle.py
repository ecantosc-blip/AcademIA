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


def prioridad_estudio_combinada(conn, estudiante_id: int) -> dict:
    """Prioridad de estudio entre las materias EN CURSO: combina, con el MISMO peso, las tareas
    vencidas sin entregar y el promedio de Moodle (la mas baja/el mas bajo primero). Decision
    explicita del usuario -no un reglamento, a diferencia del umbral de asistencia de
    core/seguimiento.py-, confirmada antes de programarla: quiere una prioridad calculada, no solo
    los datos sueltos, y que ambos factores pesen igual.

    Se usan dos funciones que ya existian (clasificar_tareas y promedios() de este mismo modulo,
    ya usadas en el plan de estudio y el resumen semanal) en vez de un calculo nuevo: asi no hay dos
    formas distintas de contar "tareas atrasadas" o "promedio" en la app.

    Metodo explicable (Borda) en vez de una formula con pesos inventados: se ordenan las materias
    por separado segun cada factor y se suma la posicion en cada orden; el numero mas bajo es la
    materia con mas prioridad. Una materia sin promedio de Moodle sincronizado no se puede comparar
    en igualdad de condiciones con las que si lo tienen, asi que se excluye del ranking.

    Devuelve {"hay_materias": bool, "ranking": [{"materia_id","materia","atrasadas","promedio",
    "puesto"}, ...] ya ordenado de mas a menos prioridad, "sin_promedio": [nombres de materias en
    curso sin promedio]}. "hay_materias" distingue "no cursa nada ahora" de "cursa materias pero
    ninguna tiene promedio todavia"."""
    from core.tareas import clasificar_tareas

    materias = conn.execute("""
        SELECT mat.materia_id AS materia_id, m.nombre AS materia
        FROM matricula mat JOIN materia m ON m.id = mat.materia_id
        WHERE mat.estudiante_id = ? AND mat.estado = 'en_curso'
        ORDER BY m.nombre
    """, (estudiante_id,)).fetchall()
    if not materias:
        return {"hay_materias": False, "ranking": [], "sin_promedio": []}

    atrasadas_por_materia = {}
    for t in clasificar_tareas(conn, estudiante_id)["vencidas_sin_entregar"]:
        atrasadas_por_materia[t["materia_id"]] = atrasadas_por_materia.get(t["materia_id"], 0) + 1
    promedios_ = promedios(conn, estudiante_id)

    datos = [{"materia": m["materia"], "materia_id": m["materia_id"],
              "atrasadas": atrasadas_por_materia.get(m["materia_id"], 0),
              "promedio": promedios_.get(m["materia_id"])} for m in materias]
    con_promedio = [d for d in datos if d["promedio"] is not None]
    sin_promedio = [d["materia"] for d in datos if d["promedio"] is None]
    if not con_promedio:
        return {"hay_materias": True, "ranking": [], "sin_promedio": sin_promedio,
                "con_atrasadas_sin_promedio": [d for d in datos if d["atrasadas"]]}

    por_atrasadas = sorted(con_promedio, key=lambda d: (-d["atrasadas"], d["materia"]))
    por_promedio = sorted(con_promedio, key=lambda d: (d["promedio"], d["materia"]))
    rango_atrasadas = {d["materia_id"]: i for i, d in enumerate(por_atrasadas)}
    rango_promedio = {d["materia_id"]: i for i, d in enumerate(por_promedio)}
    for d in con_promedio:
        d["puesto"] = rango_atrasadas[d["materia_id"]] + rango_promedio[d["materia_id"]]
    con_promedio.sort(key=lambda d: (d["puesto"], d["materia"]))
    return {"hay_materias": True, "ranking": con_promedio, "sin_promedio": sin_promedio}
