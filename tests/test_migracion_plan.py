"""Migracion 5 (plan de estudio y alertas): base nueva, base con datos, cascada y restricciones."""
import sqlite3

import pytest

from db.setup import init_db, get_connection
from test_migracion_entrega import _base_version_2

NUEVAS = {"preferencia", "dificultad_materia", "plan_excluido", "alerta"}


def _tablas(c):
    return {r["name"] for r in c.execute("select name from sqlite_master where type='table'")}


def test_base_nueva_tiene_las_tablas(bd_vacia):
    c = get_connection()
    assert NUEVAS <= _tablas(c)
    assert 5 in {r["version"] for r in c.execute("select version from schema_version")}
    c.close()


def test_base_con_datos_las_recibe_sin_perder_nada(ruta_bd):
    _base_version_2(ruta_bd)
    init_db()
    c = get_connection()
    assert NUEVAS <= _tablas(c)
    assert c.execute("select count(*) from tarea").fetchone()[0] == 3
    assert c.execute("select count(*) from estudiante").fetchone()[0] == 2
    assert all(c.execute(f"select count(*) from {t}").fetchone()[0] == 0 for t in NUEVAS)
    init_db()                                   # repetir es inocuo
    c.close()


def test_borrar_al_estudiante_borra_sus_preferencias_alertas_y_exclusiones(bd_vacia):
    c = get_connection()
    c.execute("PRAGMA foreign_keys = ON")
    c.execute("insert into estudiante(id,nombre) values (5,'X')")
    c.execute("insert into materia(id,nombre) values (1,'M')")
    c.execute("insert into preferencia values (5,'minutos_por_tarea','60')")
    c.execute("insert into dificultad_materia values (5,1,3)")
    c.execute("insert into plan_excluido values (5,'t:1')")
    c.execute("insert into alerta(estudiante_id,tipo,clave,mensaje,creada_en) values (5,'vencida','1:x','m','2026-10-30T10:00:00')")
    c.commit()
    c.execute("delete from estudiante where id=5")
    c.commit()
    assert all(c.execute(f"select count(*) from {t}").fetchone()[0] == 0 for t in NUEVAS)
    c.close()


def test_restricciones(bd_vacia):
    c = get_connection()
    c.execute("insert into estudiante(id,nombre) values (5,'X')")
    c.execute("insert into materia(id,nombre) values (1,'M')")
    with pytest.raises(sqlite3.IntegrityError):
        c.execute("insert into dificultad_materia values (5,1,4)")          # nivel fuera de 1..3
    with pytest.raises(sqlite3.IntegrityError):
        c.execute("insert into alerta(estudiante_id,tipo,clave,mensaje,creada_en) values (5,'inventada','k','m','x')")
    c.execute("insert into alerta(estudiante_id,tipo,clave,mensaje,creada_en) values (5,'vencida','k','m','x')")
    with pytest.raises(sqlite3.IntegrityError):                              # la misma alerta no se repite
        c.execute("insert into alerta(estudiante_id,tipo,clave,mensaje,creada_en) values (5,'vencida','k','otra','y')")
    c.close()
