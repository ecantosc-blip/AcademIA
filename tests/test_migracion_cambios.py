"""Migracion 4 (detector de cambios): base nueva, base existente con datos y repeticion."""
import sqlite3

from db.setup import init_db, get_connection, MIGRACIONES
from test_migracion_entrega import _base_version_2


def _tablas(c):
    return {r["name"] for r in c.execute("select name from sqlite_master where type='table'")}


def test_base_nueva_tiene_las_tablas_del_detector(bd_vacia):
    c = get_connection()
    assert {"foto_estado", "cambio"} <= _tablas(c)
    assert 4 in {r["version"] for r in c.execute("select version from schema_version")}
    assert max(m[0] for m in MIGRACIONES) >= 4
    c.close()


def test_base_con_datos_recibe_las_tablas_sin_perder_nada(ruta_bd):
    _base_version_2(ruta_bd)
    init_db()
    c = get_connection()
    assert {"foto_estado", "cambio"} <= _tablas(c)
    assert c.execute("select count(*) from tarea").fetchone()[0] == 3
    assert c.execute("select count(*) from entrega").fetchone()[0] == 3
    assert c.execute("select count(*) from estudiante").fetchone()[0] == 2
    # ningun "cambio" inventado por actualizar
    assert c.execute("select count(*) from cambio").fetchone()[0] == 0
    assert c.execute("select count(*) from foto_estado").fetchone()[0] == 0
    c.close()


def test_aplicar_dos_veces_es_inocuo(ruta_bd):
    _base_version_2(ruta_bd)
    init_db()
    c = get_connection()
    c.execute("insert into foto_estado values (77,'moodle','2026-10-23T10:00:00','{}')")
    c.commit()
    c.close()
    init_db()
    c = get_connection()
    assert c.execute("select count(*) from foto_estado").fetchone()[0] == 1
    assert [r["version"] for r in c.execute("select version from schema_version order by 1")] == list(range(1, max(m[0] for m in MIGRACIONES) + 1))
    c.close()


def test_borrar_estudiante_borra_fotos_y_cambios(bd_vacia):
    c = get_connection()
    c.execute("PRAGMA foreign_keys = ON")
    c.execute("insert into estudiante(id,nombre) values (5,'X')")
    c.execute("insert into foto_estado values (5,'moodle','2026-10-23T10:00:00','{}')")
    c.execute("insert into cambio(estudiante_id,plataforma,tipo,detectado_en) values (5,'moodle','tarea_nueva','2026-10-23T10:00:00')")
    c.commit()
    c.execute("delete from estudiante where id = 5")
    c.commit()
    assert c.execute("select count(*) from foto_estado").fetchone()[0] == 0
    assert c.execute("select count(*) from cambio").fetchone()[0] == 0
    c.close()


def test_el_tipo_de_cambio_se_valida(bd_vacia):
    import pytest
    c = get_connection()
    c.execute("insert into estudiante(id,nombre) values (5,'X')")
    with pytest.raises(sqlite3.IntegrityError):
        c.execute("insert into cambio(estudiante_id,plataforma,tipo,detectado_en) values (5,'moodle','inventado','x')")
    c.close()
