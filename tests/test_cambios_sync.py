"""Detector de cambios dentro de la sincronizacion completa (Moodle y ERP simulados).
Lo central: solo se compara entre sincronizaciones EXITOSAS y un fallo no se lee como cambio."""
from datetime import datetime, timezone

import pytest

from core import cambios
from core.sync_all import sync
from db.setup import get_connection

E = 77
TOKEN = "a1b2c3d4e5f60718293a4b5c6d7e8f90"


def _ts(dia):
    return int(datetime(2026, 11, dia, 15, 0, tzinfo=timezone.utc).timestamp())


class Moodle:
    token = "t"

    def __init__(self, tareas=None, eventos=None, notas=None, falla_en=None):
        self.tareas = tareas if tareas is not None else [(5, "Tarea A", 10)]
        self.eventos = eventos if eventos is not None else []
        self.notas = notas if notas is not None else [("Total N1", "10,00")]
        self.falla_en = falla_en

    def get_site_info(self):
        return {"fullname": "Prueba", "userid": 55}

    def get_courses(self):
        return [{"id": 1, "fullname": "MATERIA UNO - [A1] - A1 - SOFTWARE2019"}]

    def get_teachers(self, cid):
        return []

    def get_assignments(self, ids):
        return {"courses": [{"id": 1, "assignments": [
            {"id": i, "name": n, "duedate": _ts(d)} for i, n, d in self.tareas]}]}

    def get_submission_status(self, assign_id, userid):
        return {"lastattempt": {"submission": {"status": "new"}}}

    def get_upcoming_events(self):
        return {"events": [{"name": n, "timestart": _ts(d), "eventtype": "course"} for n, d in self.eventos]}

    def get_all_grades(self):
        if self.falla_en == "notas":
            raise RuntimeError(f"Moodle caido wstoken={TOKEN}")
        return [{"curso": "MATERIA UNO", "item": i, "calificacion": c, "porcentaje": ""} for i, c in self.notas]


class SGA:
    access_token = "x"

    def __init__(self, nota=60.0, asistencia=90.0, falla=False):
        self.nota, self.asistencia, self.falla = nota, asistencia, falla

    def cambiar_a_perfil_regular(self):
        return False

    def get_materias_parseadas(self):
        if self.falla:
            raise RuntimeError("ERP caido")
        return [{"nombre": "MATERIA UNO", "nota": self.nota, "asistencia": self.asistencia}]

    def get_horario_parseado(self):
        return []

    def get_registro_parseado(self, a):
        return []

    def get_malla_parseada(self):
        return []


def correr(moodle, sga=None):
    sync(estudiante_id=E, moodle_client=moodle, sga_client=sga or SGA(), sga_alumno_id="")


def cambios_de(c, plataforma=None):
    sql = "select tipo, item, antes, despues from cambio where estudiante_id=?"
    args = [E]
    if plataforma:
        sql += " and plataforma=?"
        args.append(plataforma)
    return sorted(tuple(r) for r in c.execute(sql + " order by tipo, item", args))


@pytest.fixture
def con(bd_vacia):
    c = get_connection()
    yield c
    c.close()


def test_primera_sincronizacion_es_solo_punto_de_partida_y_repetir_no_inventa(con):
    correr(Moodle())
    assert cambios_de(con) == []
    correr(Moodle())
    correr(Moodle())
    assert cambios_de(con) == []
    assert con.execute("select count(*) from foto_estado").fetchone()[0] == 2     # moodle y sga


def test_cambios_reales_entre_sincronizaciones_exitosas(con):
    correr(Moodle())
    correr(Moodle(tareas=[(5, "Tarea A", 12), (6, "Tarea B", 20)],
                  eventos=[("Examen parcial", 15)],
                  notas=[("Total N1", "9,00"), ("Total N2", "7,50")]),
          SGA(nota=71.5, asistencia=95.0))
    assert cambios_de(con) == [
        ("evento_nuevo", "Examen parcial", None, "2026-11-15T10:00:00"),
        ("nota_cambio", "Total N1", "10,00", "9,00"),
        ("nota_nueva", "Total N2", None, "7,50"),
        ("sga_asistencia", None, "90.0", "95.0"),
        ("sga_nota_final", None, "60.0", "71.5"),
        ("tarea_fecha", "Tarea A", "2026-11-10T10:00:00", "2026-11-12T10:00:00"),
        ("tarea_nueva", "Tarea B", None, "2026-11-20T10:00:00"),
    ]
    n = len(cambios_de(con))
    correr(Moodle(tareas=[(5, "Tarea A", 12), (6, "Tarea B", 20)], eventos=[("Examen parcial", 15)],
                  notas=[("Total N1", "9,00"), ("Total N2", "7,50")]), SGA(nota=71.5, asistencia=95.0))
    assert len(cambios_de(con)) == n                    # misma foto: nada nuevo


def test_una_lectura_de_moodle_fallida_no_es_un_cambio_ni_deja_datos_a_medias(con):
    correr(Moodle())
    foto = con.execute("select datos from foto_estado where plataforma='moodle'").fetchone()[0]
    # falla a mitad (despues de guardar tareas): hay una tarea nueva en Moodle, pero la lectura no termina
    correr(Moodle(tareas=[(5, "Tarea A", 10), (6, "Tarea B", 20)], falla_en="notas"))
    estados = [r["estado"] for r in con.execute("select estado from sesion_extraccion where plataforma='moodle' order by id")]
    assert estados == ["exitosa", "fallida"]
    assert cambios_de(con, "moodle") == []
    assert con.execute("select datos from foto_estado where plataforma='moodle'").fetchone()[0] == foto
    assert con.execute("select count(*) from tarea").fetchone()[0] == 1          # nada parcial guardado
    detalle = con.execute("select detalle from sesion_extraccion where estado='fallida'").fetchone()[0]
    assert TOKEN not in detalle
    # la siguiente lectura buena SI informa la tarea nueva (el fallo no la escondio) y una sola vez
    correr(Moodle(tareas=[(5, "Tarea A", 10), (6, "Tarea B", 20)]))
    assert cambios_de(con, "moodle") == [("tarea_nueva", "Tarea B", None, "2026-11-20T10:00:00")]
    correr(Moodle(tareas=[(5, "Tarea A", 10), (6, "Tarea B", 20)]))
    assert len(cambios_de(con, "moodle")) == 1


def test_un_fallo_del_erp_no_toca_la_foto_y_no_cuenta_como_cambio(con):
    correr(Moodle(), SGA(nota=60.0))
    correr(Moodle(), SGA(falla=True))
    assert cambios_de(con, "sga") == []
    assert con.execute("select nota_final from matricula where estudiante_id=?", (E,)).fetchone()[0] == 60.0
    correr(Moodle(), SGA(nota=65.0))
    assert cambios_de(con, "sga") == [("sga_nota_final", None, "60.0", "65.0")]


def test_si_el_detector_falla_la_sincronizacion_sigue_siendo_exitosa(con, monkeypatch):
    correr(Moodle())
    monkeypatch.setattr(cambios, "registrar", lambda *a, **k: (_ for _ in ()).throw(RuntimeError(f"x wstoken={TOKEN}")))
    correr(Moodle(tareas=[(5, "Tarea A", 10), (6, "Tarea B", 20)]))
    filas = con.execute("select estado from sesion_extraccion where plataforma='moodle' order by id").fetchall()
    assert [f["estado"] for f in filas] == ["exitosa", "exitosa"]
    assert con.execute("select count(*) from tarea").fetchone()[0] == 2            # los datos si se guardaron
