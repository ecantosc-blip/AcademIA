"""Script de verificacion del ERP (pesos de las notas): probado con un ERP simulado."""
import importlib
import json
import pathlib

import pytest

DIAG = pathlib.Path(__file__).resolve().parents[1] / "herramientas" / "diagnostico"
NOMBRE = "JUAN PEREZ SECRETO"
CEDULA = "0912345678"
MATERIA = "FÍSICA RESERVADA"
TOKEN = "eyJhbGciOiJIUzI1NiJ9.eyJleHAiOjF9.abcdefghijklmnop"


@pytest.fixture
def verificar(monkeypatch):
    monkeypatch.syspath_prepend(str(DIAG))
    return importlib.import_module("verificar_erp")


class ERP:
    def __init__(self, con_pesos=True, materias_error=None, registro_error=None):
        self.con_pesos, self.materias_error, self.registro_error = con_pesos, materias_error, registro_error

    def cambiar_a_perfil_regular(self):
        return False

    def get_materias(self):
        if self.materias_error:
            raise self.materias_error
        m = {"display": f"{MATERIA} {NOMBRE}", "notafinal": 8.5, "asistenciafinal": 90,
             "materia": {"asignatura": {"nombre": MATERIA}}, "matricula": {"inscripcion": {"cedula": CEDULA, "nombre": NOMBRE}}}
        if self.con_pesos:
            m["modeloevaluativo"] = {"detalle": [{"nombre": "ACD", "pesoporcentaje": 30.0, "notamaxima": 30},
                                                 {"nombre": "EXAMEN", "pesoporcentaje": "40,00 %", "notamaxima": 40}]}
        return {"data": {"eMateriasAsignadas": [m]}}

    def get_alumno_id_automatico(self):
        return "123"

    def get_registro_academico(self, alumno_id):
        if self.registro_error:
            raise self.registro_error
        return {"data": {"eRecordaAademicos": [{"asignatura": {"nombre": MATERIA}, "nota": 80, "creditos": 4}]}}


def test_con_pesos_muestra_numeros_y_nombres_de_campos_sin_datos_personales(verificar, capsys, tmp_path):
    assert verificar.main(ERP(con_pesos=True), carpeta_local=str(tmp_path)) == 0
    out = capsys.readouterr().out
    assert "modeloevaluativo.detalle[].pesoporcentaje = 30.0" in out
    assert "pesoporcentaje = 40.0" in out and "notamaxima = 40.0" in out
    assert "Hay numeros con nombre de peso" in out
    for secreto in (NOMBRE, CEDULA, MATERIA, "ACD", "EXAMEN"):
        assert secreto not in out
    guardado = (tmp_path / "erp_campos.json").read_text(encoding="utf-8")
    for secreto in (NOMBRE, CEDULA, MATERIA):
        assert secreto not in guardado
    assert any(r.endswith("modeloevaluativo.detalle[].pesoporcentaje")
               for r in json.loads(guardado)["materias_actuales"])


def test_sin_pesos_lo_dice_claramente(verificar, capsys, tmp_path):
    assert verificar.main(ERP(con_pesos=False), carpeta_local=str(tmp_path)) == 0
    out = capsys.readouterr().out
    assert "Ningun campo con nombre de peso" in out
    assert "El ERP NO entrega pesos ni maximos" in out
    assert NOMBRE not in out and CEDULA not in out


def test_errores_no_filtran_tokens(verificar, capsys, tmp_path):
    assert verificar.main(ERP(materias_error=RuntimeError(f"fallo Bearer {TOKEN}")), carpeta_local=str(tmp_path)) == 1
    assert TOKEN not in capsys.readouterr().out
    assert verificar.main(ERP(registro_error=RuntimeError(f"fallo token={TOKEN}")), carpeta_local=str(tmp_path)) == 0
    out = capsys.readouterr().out
    assert TOKEN not in out and "No pude leerlo" in out


def test_analizar_ignora_booleanos_y_textos(verificar):
    a = verificar.analizar({"peso": True, "porcentaje": "abc", "maximo": "10,00", "x": [{"pesoextra": 5}, {"pesoextra": 5}]})
    assert a["numeros"] == [("maximo", 10.0), ("x[].pesoextra", 5.0)]


def test_el_token_pedido_no_se_imprime(verificar, monkeypatch, capsys, tmp_path):
    import comun
    monkeypatch.setattr(comun.getpass, "getpass", lambda prompt="": f"Bearer {TOKEN}")
    llamado = {}

    class Cliente(ERP):
        def __init__(self, base, token, refresh=None):
            super().__init__(con_pesos=False)
            llamado["token"] = token
    monkeypatch.setattr(verificar, "SGAClient", Cliente)
    monkeypatch.setattr(verificar, "segundos_para_vencer", lambda t: 3600)
    assert verificar.main(carpeta_local=str(tmp_path)) == 0
    assert llamado["token"] == TOKEN                       # sin la palabra Bearer
    assert TOKEN not in capsys.readouterr().out


def test_numeros_generales_del_periodo_no_se_confunden_con_pesos(verificar, capsys, tmp_path):
    class ERPPeriodo(ERP):
        def get_materias(self):
            r = super().get_materias()
            r["data"]["ePeriodo"] = {"valor_maximo": 10.0, "porcentaje_gratuidad": 10.0}
            return r
    assert verificar.main(ERPPeriodo(con_pesos=False), carpeta_local=str(tmp_path)) == 0
    out = capsys.readouterr().out
    assert "data.ePeriodo.valor_maximo = 10.0" in out
    assert "El ERP NO entrega pesos" in out and "datos generales" in out
    assert "Hay numeros con nombre de peso" not in out
