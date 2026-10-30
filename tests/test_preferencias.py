"""Preferencias del plan y de las alertas: validacion estricta, nada se inventa."""
import pytest

from core import preferencias as P
from db.setup import get_connection

E = 77


@pytest.fixture
def con(bd_demo):
    c = get_connection()
    c.execute("PRAGMA foreign_keys = ON")
    yield c
    c.close()


def test_valores_de_partida_y_sin_disponibilidad_inventada(con):
    p = P.obtener(con, E)
    assert p["disponibilidad"] == {}                       # nunca se supone cuanto tiempo tiene la persona
    assert (p["minutos_por_tarea"], p["minutos_evaluacion"], p["alerta_horas"]) == (60, 120, 48)
    assert p["asistencia_minima"] is None and p["alertas_tipos"] == P.TIPOS_ALERTA


def test_guardar_y_leer(con):
    p = P.guardar(con, E, {"disponibilidad": {"lunes": 120, "martes": "90", "jueves": 0},
                           "minutos_por_tarea": "45", "alerta_horas": 24, "asistencia_minima": "70"})
    assert p["disponibilidad"] == {"lunes": 120, "martes": 90}      # un 0 equivale a "ese dia no"
    assert p["minutos_por_tarea"] == 45 and p["alerta_horas"] == 24 and p["asistencia_minima"] == 70.0
    P.guardar(con, E, {"asistencia_minima": None})
    assert P.obtener(con, E)["asistencia_minima"] is None


@pytest.mark.parametrize("cambios", [
    {"disponibilidad": {"funday": 60}}, {"disponibilidad": {"lunes": 721}}, {"disponibilidad": {"lunes": -5}},
    {"disponibilidad": {"lunes": "mucho"}}, {"disponibilidad": [60]}, {"minutos_por_tarea": 14},
    {"minutos_por_tarea": 60.5}, {"minutos_por_tarea": True}, {"alerta_horas": 5}, {"alertas_tipos": ["otra"]},
    {"asistencia_minima": 101}, {"inventada": 1}, {}, None, "x",
])
def test_valores_invalidos_se_rechazan(con, cambios):
    with pytest.raises(ValueError):
        P.guardar(con, E, cambios)


def test_si_un_valor_falla_no_se_guarda_ninguno(con):
    with pytest.raises(ValueError):
        P.guardar(con, E, {"minutos_por_tarea": 90, "alerta_horas": 5})
    assert P.obtener(con, E)["minutos_por_tarea"] == 60


def test_cada_estudiante_tiene_las_suyas(con):
    con.execute("insert into estudiante(id,nombre) values (78,'OTRA')")
    P.guardar(con, E, {"minutos_por_tarea": 90})
    assert P.obtener(con, 78)["minutos_por_tarea"] == 60


def test_dificultad_solo_de_materias_propias(con):
    P.guardar_dificultad(con, E, 1, 3)
    P.guardar_dificultad(con, E, 1, 2)
    assert P.dificultades(con, E) == {1: 2}
    P.guardar_dificultad(con, E, 1, None)
    assert P.dificultades(con, E) == {}
    con.execute("insert into estudiante(id,nombre) values (78,'OTRA')")
    for materia, nivel in ((1, 4), (1, 0), (999, 2), ("x", 2)):
        with pytest.raises(ValueError):
            P.guardar_dificultad(con, 78 if materia == 1 and nivel == 2 else E, materia, nivel)
    with pytest.raises(ValueError):
        P.guardar_dificultad(con, 78, 1, 2)                 # 78 no cursa la materia 1


def test_excluir_e_incluir_del_plan(con):
    P.excluir(con, E, "t:5")
    P.excluir(con, E, "t:5")                                 # repetir no duplica
    P.excluir(con, E, "e:Examen parcial|2026-11-10T09:00:00")
    assert P.excluidos(con, E) == {"t:5", "e:Examen parcial|2026-11-10T09:00:00"}
    P.incluir(con, E, "t:5")
    assert P.excluidos(con, E) == {"e:Examen parcial|2026-11-10T09:00:00"}
    for mala in ("x", "t:", "t:abc", "", None, "e:" + "a" * 300):
        with pytest.raises(ValueError):
            P.excluir(con, E, mala)
