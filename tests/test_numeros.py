"""Lectura de numeros de Moodle: sin inventar valores."""
import pytest

from core.numeros import a_numero, a_rango, es_porcentaje


@pytest.mark.parametrize("texto,esperado", [
    ("10,00", 10.0), ("6,67", 6.67), ("10.00", 10.0), ("0", 0.0), ("100", 100.0),
    ("80,00 %", 80.0), ("80%", 80.0), (" 7,5 ", 7.5), (" 8,25 ", 8.25),
    ("1.234,56", 1234.56), ("1,234.56", 1234.56), ("1.234.567", 1234567.0),
    ("-5,00", -5.0), ("−5,00", -5.0), (7, 7.0), (7.5, 7.5), ("<b>9,50</b>", 9.5),
])
def test_numeros_validos(texto, esperado):
    assert a_numero(texto) == pytest.approx(esperado)


@pytest.mark.parametrize("texto", [
    None, "", "-", "–", "—", "  ", "N/A", "sin calificar", "abc", "10,00 puntos", "1,234,567",
    "1.2.3", "nan", "inf", float("nan"), float("inf"), True, "--5", "5,",
])
def test_lo_que_no_es_un_numero_da_none_y_no_cero(texto):
    assert a_numero(texto) is None


def test_none_no_es_cero():
    assert a_numero("-") is None and a_numero("0,00") == 0.0


def test_es_porcentaje():
    assert es_porcentaje("80,00 %") and not es_porcentaje("8,00") and not es_porcentaje(None)


@pytest.mark.parametrize("texto,esperado", [
    ("0–100", (0.0, 100.0)), ("0-100", (0.0, 100.0)), ("0 - 10,5", (0.0, 10.5)), ("0—10", (0.0, 10.0)),
])
def test_rangos(texto, esperado):
    assert a_rango(texto) == esperado


@pytest.mark.parametrize("texto", [None, "", "100", "10-0", "5-5", "a-b", "0-"])
def test_rangos_invalidos(texto):
    assert a_rango(texto) is None
