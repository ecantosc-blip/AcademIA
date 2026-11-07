"""Horario personal: validacion y hora realmente libre (franjas - clases - actividades)."""
import pytest

from core import agenda_personal as A
from db.setup import get_connection

EST = 77


@pytest.fixture
def conn(app_web):
    c = get_connection()
    yield c
    c.close()


def test_dia_y_hora_se_validan():
    assert A.normalizar_dia(" Miércoles ") == "miercoles" and A.a_minutos("18:30") == 1110 and A.a_texto(1110) == "18:30"
    for malo in ("", "25:00", "18:60", "abc", None):
        with pytest.raises(ValueError):
            A.a_minutos(malo)
    with pytest.raises(ValueError):
        A.normalizar_dia("martesito")


def test_franjas_se_reemplazan_se_unen_y_se_validan_antes_de_guardar(conn):
    n = A.guardar_franjas(conn, EST, [{"dia": "Lunes", "inicio": "18:00", "fin": "19:00"},
                                      {"dia": "lunes", "inicio": "18:30", "fin": "20:00"},
                                      {"dia": "sabado", "inicio": "09:00", "fin": "11:00"}])
    assert n == 2
    assert A.listar(conn, EST)["franjas"] == [{"dia": "lunes", "inicio": "18:00", "fin": "20:00"},
                                              {"dia": "sabado", "inicio": "09:00", "fin": "11:00"}]
    # una lista con un error no toca lo guardado
    with pytest.raises(ValueError):
        A.guardar_franjas(conn, EST, [{"dia": "lunes", "inicio": "10:00", "fin": "10:05"}])
    assert len(A.listar(conn, EST)["franjas"]) == 2
    assert A.guardar_franjas(conn, EST, []) == 0 and A.listar(conn, EST)["franjas"] == []


def test_actividades_piden_nombre_y_limitan_filas(conn):
    with pytest.raises(ValueError):
        A.guardar_actividades(conn, EST, [{"dia": "lunes", "inicio": "08:00", "fin": "12:00", "nombre": " "}])
    with pytest.raises(ValueError):
        A.guardar_franjas(conn, EST, [{"dia": "lunes", "inicio": "08:00", "fin": "09:00"}] * (A.MAX_FILAS + 1))
    A.guardar_actividades(conn, EST, [{"dia": "lunes", "inicio": "08:00", "fin": "12:00", "nombre": "  Trabajo   "}])
    assert A.listar(conn, EST)["actividades"][0]["nombre"] == "Trabajo"


def test_hora_libre_descuenta_clases_y_actividades(conn):
    # clases del demo: lunes 18:00-20:00 (automatizacion) y martes 18:00-19:00
    A.guardar_franjas(conn, EST, [{"dia": "lunes", "inicio": "16:00", "fin": "22:00"},
                                  {"dia": "martes", "inicio": "18:00", "fin": "19:00"},
                                  {"dia": "domingo", "inicio": "08:00", "fin": "12:00"}])
    A.guardar_actividades(conn, EST, [{"dia": "lunes", "inicio": "21:00", "fin": "21:30", "nombre": "Cena"},
                                      {"dia": "domingo", "inicio": "09:00", "fin": "10:00", "nombre": "Deporte"}])
    v = A.ventanas_efectivas(conn, EST)
    assert v["lunes"] == [(960, 1080), (1200, 1260), (1290, 1320)]   # 16-18, 20-21, 21:30-22
    assert v["martes"] == []                                         # toda la franja es clase
    assert v["domingo"] == [(480, 540), (600, 720)]
    assert v["miercoles"] == []


def test_sin_franjas_no_hay_ventanas_ni_se_supone_nada(conn):
    assert not A.hay_franjas(conn, EST)
    assert all(v == [] for v in A.ventanas_efectivas(conn, EST).values())
