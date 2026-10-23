"""Script de verificacion con cuenta real: probado con un Moodle simulado."""
import sys
import pathlib
import importlib

import pytest

DIAG = pathlib.Path(__file__).resolve().parents[1] / "herramientas" / "diagnostico"
CURSO = "FÍSICA SECRETA"
TAREA = "Taller privado de laboratorio"
TOKEN = "a1b2c3d4e5f60718293a4b5c6d7e8f90"


@pytest.fixture
def verificar(monkeypatch):
    monkeypatch.syspath_prepend(str(DIAG))
    return importlib.import_module("verificar_moodle")


class Moodle:
    def __init__(self, status="submitted", error=None, items=None):
        self.status, self.error = status, error
        self.items = items if items is not None else [
            {"itemtype": "mod", "itemname": TAREA, "graderaw": 8, "grademin": 0, "grademax": 10,
             "gradeformatted": "8,00", "percentageformatted": "80,00 %", "weightformatted": "20,00 %"},
            {"itemtype": "mod", "itemname": "Otra", "graderaw": 5, "grademin": 0, "grademax": 20,
             "gradeformatted": "5,00", "percentageformatted": "25,00 %", "weightformatted": "30,00 %"},
        ]

    def get_site_info(self):
        return {"userid": 5, "fullname": "Nombre Secreto"}

    def get_courses(self):
        return [{"id": 1, "fullname": CURSO}, {"id": 2, "fullname": "OTRO CURSO"}]

    def get_assignments(self, ids):
        return {"courses": [{"id": 1, "assignments": [{"id": 10, "name": TAREA}]},
                            {"id": 2, "assignments": [{"id": 20, "name": "x", "nosubmissions": 1}]}]}

    def get_submission_status(self, aid, uid):
        if self.error:
            raise self.error
        return {"lastattempt": {"submission": {"status": self.status}, "submissionsenabled": True}}

    def get_grades(self, cid, uid):
        return {"usergrades": [{"gradeitems": self.items}]}


def test_funcion_disponible_y_porcentaje_es_avance(verificar, capsys):
    items = [{"itemtype": "mod", "itemname": TAREA, "graderaw": 8, "grademin": 0, "grademax": 10,
              "percentageformatted": "80,00 %", "weightformatted": "20,00 %"},
             {"itemtype": "mod", "itemname": "B", "graderaw": 5, "grademin": 0, "grademax": 20,
              "percentageformatted": "25,00 %", "weightformatted": "30,00 %"}]
    assert verificar.main(Moodle(items=items)) == 0
    out = capsys.readouterr().out
    assert "status='submitted'" in out and "interpretado='entregada'" in out
    assert "sin tareas con entrega en linea" in out
    assert "percentageformatted es AVANCE" in out
    assert "peso=20.0" in out and "calculado=80.0" in out
    for secreto in (CURSO, TAREA, "Nombre Secreto"):
        assert secreto not in out


def test_si_el_porcentaje_no_coincide_no_se_afirma_que_sea_avance(verificar, capsys):
    items = [{"itemtype": "mod", "itemname": "A", "graderaw": 8, "grademin": 0, "grademax": 10,
              "percentageformatted": "20,00 %", "weightformatted": "20,00 %"}]
    verificar.main(Moodle(items=items))
    out = capsys.readouterr().out
    assert "NO coincide siempre" in out and "AVANCE" not in out


def test_funcion_no_disponible_se_dice_sin_filtrar_el_token(verificar, capsys):
    err = RuntimeError(f"Error de red al llamar a Moodle: ...?wstoken={TOKEN}&wsfunction=mod_assign_get_submission_status")
    verificar.main(Moodle(error=err))
    out = capsys.readouterr().out
    assert "LA FUNCION NO RESPONDE" in out and "Sin verificar" in out
    assert TOKEN not in out


def test_status_desconocido_se_reporta_como_tal(verificar, capsys):
    verificar.main(Moodle(status="reopened"))
    out = capsys.readouterr().out
    assert "interpretado=None" in out and "reopened" in out


def test_limita_los_numeros_a_tres_items(verificar, capsys):
    items = [{"itemtype": "mod", "itemname": f"n{i}", "graderaw": i, "grademin": 0, "grademax": 10,
              "percentageformatted": f"{i*10},00 %"} for i in range(1, 8)]
    verificar.main(Moodle(items=items))
    out = capsys.readouterr().out
    assert out.count("nota=") == 3


def test_analizar_item_con_rango_en_texto(verificar):
    a = verificar.analizar_item({"gradeformatted": "6,67", "rangeformatted": "0–10", "percentageformatted": "66,70 %"})
    assert a["maximo"] == 10.0 and a["percentage_calculado"] == 66.7 and a["percentage_es_avance"] is True


def test_sin_conexion_devuelve_error_limpio(verificar, capsys):
    class Roto(Moodle):
        def get_site_info(self):
            raise RuntimeError(f"fallo wstoken={TOKEN}")
    assert verificar.main(Roto()) == 1
    assert TOKEN not in capsys.readouterr().out
