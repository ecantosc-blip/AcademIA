"""Esquema 3FN, migraciones, sincronizacion simulada sin duplicados y herramientas del chat."""
import os
import sqlite3
import pathlib

import pytest

from db.setup import init_db, get_connection

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


class MoodleFalso:
    token = "t"

    def get_site_info(self):
        return {"fullname": "Erick Prueba", "userid": 77}

    def get_courses(self):
        return [{"id": 2067, "fullname": "SISTEMA DE AUTOMATIZACIÓN INDUSTRIAL - [IS08SA] - A1 - SOFTWARE2019"},
                {"id": 721, "fullname": "DISEÑO DE INTEGRACIÓN CURRICULAR - [FCI08IC] - A1 - SOFTWARE2019"}]

    def get_teachers(self, cid):
        return [{"nombre": "Ana Perez", "correo": "ana@x.ec"}, {"nombre": "Luis Mora", "correo": None}]

    def get_assignments(self, ids):
        return {"courses": [{"id": 2067, "assignments": [{"id": 5, "name": "Taller 1", "duedate": 1790000000}]}]}

    def get_upcoming_events(self):
        return {"events": [{"name": "Examen", "timestart": 1790000000, "eventtype": "examen"},
                           {"name": "Taller 1", "timestart": 1790000000}]}

    def get_all_grades(self):
        return [{"curso": "SISTEMA DE AUTOMATIZACIÓN INDUSTRIAL", "item": "Total N2", "calificacion": "6,67", "porcentaje": ""}]


class SGAFalso:
    access_token = "x"

    def cambiar_a_perfil_regular(self):
        return False

    def get_materias_parseadas(self):
        return [{"nombre": "SISTEMA DE AUTOMATIZACIÓN INDUSTRIAL", "nota": 6.67, "asistencia": 100.0}]

    def get_horario_parseado(self):
        return [{"materia": "SISTEMA DE AUTOMATIZACIÓN INDUSTRIAL", "dia": "lunes", "hora_inicio": "18:00",
                 "hora_fin": "20:00", "aula": "A1"}]

    def get_registro_parseado(self, a):
        return []

    def get_malla_parseada(self):
        return [{"materia": "SISTEMA DE AUTOMATIZACIÓN INDUSTRIAL", "nivel_orden": 8, "creditos": 4, "prerrequisitos": []}]


def test_base_legada_con_datos_migra(ruta_bd):
    viejo = sqlite3.connect(ruta_bd)
    viejo.executescript((FIXTURES / "esquema_legado.sql").read_text(encoding="utf-8"))
    viejo.execute("INSERT INTO estudiante(id,nombre,total_adeudado) VALUES (1,'X',5)")
    viejo.commit()
    viejo.close()
    init_db()
    c = get_connection()
    tablas = {r[0] for r in c.execute("select name from sqlite_master where type='table'")}
    assert "rubro_financiero" not in tablas and "nota" not in tablas
    assert {"docente", "materia_docente", "entrega"} <= tablas
    assert c.execute("select count(*) from estudiante").fetchone()[0] == 0
    init_db()   # idempotente
    c.close()


def test_sincronizacion_dos_veces_no_duplica(bd_vacia):
    from core.sync_all import sync
    for _ in range(2):
        sync(estudiante_id=77, moodle_client=MoodleFalso(), sga_client=SGAFalso(), sga_alumno_id="")
    c = get_connection()
    q = lambda s: c.execute(s).fetchone()["n"]
    assert q("select count(*) n from docente") == 2
    assert q("select count(*) n from materia_docente") == 4
    assert q("select count(*) n from tarea") == 1, "tareas duplicadas"
    assert q("select count(*) n from componente_nota") == 1
    assert q("select count(*) n from sesion_extraccion where estado='fallida'") == 0
    assert q("select count(*) n from evento") == 1   # 'Taller 1' se filtra por ser tarea
    c.close()


def test_claves_foraneas_y_unicidad(bd_demo):
    c = get_connection()
    with pytest.raises(sqlite3.IntegrityError):
        c.execute("insert into matricula(estudiante_id,materia_id) values (999,1)")
    c.execute("insert into estudiante(id,nombre) values (5,'Y')")
    c.execute("insert into matricula(estudiante_id,materia_id,periodo) values (5,1,'P1')")
    with pytest.raises(sqlite3.IntegrityError):
        c.execute("insert into matricula(estudiante_id,materia_id,periodo) values (5,1,'P1')")
    c.close()


def test_toda_herramienta_del_chat_tiene_metodo(bd_demo):
    from core.query_engine import QueryEngine
    from core.assistant import TOOLS
    e = QueryEngine(77)
    # el carne lo maneja el asistente (boton de descarga), no el motor de consultas
    from core.plan_chat import NOMBRES as DEL_PLAN   # las del plan viven en core/plan_chat.py
    nombres = [t["name"] for t in TOOLS if t["name"] != "carnet_estudiantil" and t["name"] not in DEL_PLAN]
    faltan = [n for n in nombres if not hasattr(e, n)]
    assert not faltan, faltan
    assert not hasattr(e, "finanzas")
