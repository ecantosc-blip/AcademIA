from core import notas_moodle as N
from db.setup import get_connection

EST = 77


def _poner(conn, matricula_id, items):
    conn.execute("DELETE FROM componente_nota WHERE matricula_id = ?", (matricula_id,))
    for it, pct in items:
        conn.execute("INSERT INTO componente_nota(matricula_id, nombre_item, calificacion, porcentaje) VALUES (?,?,?,?)",
                     (matricula_id, it, "1", pct))
    conn.commit()


def test_total_del_curso_manda_y_sin_dato_queda_en_none(app_web):
    c = get_connection()
    try:
        m1 = c.execute("SELECT id FROM matricula WHERE estudiante_id=? AND materia_id=1", (EST,)).fetchone()[0]
        _poner(c, m1, [("Total N1", "100,00 %"), ("Total del curso", "66,70 %")])
        assert N.promedios(c, EST)[1] == 66.7
        _poner(c, m1, [("Total N1", "80,00 %"), ("Total N2", "60,00 %"), ("N1 - TAREA_1", "10,00 %")])
        assert N.promedios(c, EST)[1] == 70.0                       # promedio de los totales por bloque
        _poner(c, m1, [("Total N1", "-"), ("Total del curso", "")])
        assert N.promedios(c, EST)[1] is None                       # sin dato: no se inventa
    finally:
        c.close()


def test_prioridad_ordena_de_menor_a_mayor_y_no_reordena_si_todo_es_igual(app_web):
    c = get_connection()
    try:
        m = {i: c.execute("SELECT id FROM matricula WHERE estudiante_id=? AND materia_id=?", (EST, i)).fetchone()[0] for i in (1, 2)}
        _poner(c, m[1], [("Total del curso", "80,00 %")])
        _poner(c, m[2], [("Total del curso", "55,00 %")])
        assert N.prioridad(c, EST) == {2: 1, 1: 2}
        _poner(c, m[2], [("Total del curso", "80,00 %")])
        assert N.prioridad(c, EST) == {}                            # todas iguales: sin prioridad
        _poner(c, m[2], [])
        assert N.prioridad(c, EST) == {}                            # una sola con dato: sin prioridad
    finally:
        c.close()
