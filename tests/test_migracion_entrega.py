"""Migracion 3 (tabla entrega): base nueva, base existente con datos, repeticion y fallo."""
import pathlib
import sqlite3

import pytest

from db.setup import init_db, get_connection, MIGRACIONES

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def _columnas(c, tabla):
    return [r["name"] for r in c.execute(f"PRAGMA table_info({tabla})")]


def _base_version_2(ruta, con_datos=True):
    """Base tal como la dejaba la version anterior (migraciones 1 y 2, entrega con 0/1)."""
    con = sqlite3.connect(ruta)
    con.executescript((FIXTURES / "esquema_v2.sql").read_text(encoding="utf-8"))
    con.executescript("""
        CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY, descripcion TEXT, aplicada TEXT NOT NULL);
        INSERT INTO schema_version VALUES (1,'x','2026-01-01'),(2,'y','2026-01-01');
    """)
    if con_datos:
        con.executescript("""
            INSERT INTO estudiante(id,nombre) VALUES (77,'X'),(78,'Y');
            INSERT INTO materia(id,nombre,moodle_course_id) VALUES (1,'M1',1);
            INSERT INTO tarea(id,materia_id,moodle_assign_id,nombre,fecha_entrega) VALUES
                (1,1,11,'T1','2026-10-30T10:00:00'),(2,1,12,'T2','2026-10-31T10:00:00'),(3,1,13,'T3',NULL);
            INSERT INTO entrega(tarea_id,estudiante_id,entregada) VALUES (1,77,1),(2,77,0),(1,78,1);
        """)
    con.commit()
    con.close()


def test_la_base_de_la_version_anterior_tiene_la_forma_vieja(ruta_bd):
    _base_version_2(ruta_bd)
    c = get_connection()
    assert "entregada" in _columnas(c, "entrega") and "estado" not in _columnas(c, "entrega")
    c.close()


def test_base_nueva_nace_con_la_forma_nueva(bd_vacia):
    c = get_connection()
    assert _columnas(c, "entrega") == ["tarea_id", "estudiante_id", "estado", "verificada_en"]
    assert c.execute("select max(version) from schema_version").fetchone()[0] == max(m[0] for m in MIGRACIONES)
    c.close()


def test_base_existente_con_datos_conserva_las_filas(ruta_bd):
    _base_version_2(ruta_bd)
    init_db()
    c = get_connection()
    assert _columnas(c, "entrega") == ["tarea_id", "estudiante_id", "estado", "verificada_en"]
    filas = {(r["tarea_id"], r["estudiante_id"]): (r["estado"], r["verificada_en"])
             for r in c.execute("select * from entrega")}
    assert filas == {(1, 77): ("entregada", None), (2, 77): ("sin_entregar", None), (1, 78): ("entregada", None)}
    # lo demas no se toca
    assert c.execute("select count(*) from tarea").fetchone()[0] == 3
    assert c.execute("select count(*) from estudiante").fetchone()[0] == 2
    assert c.execute("select version from schema_version order by version").fetchall()[-1][0] == 3
    # clave primaria, claves foraneas e indice siguen activos
    with pytest.raises(sqlite3.IntegrityError):
        c.execute("insert into entrega(tarea_id,estudiante_id,estado) values (1,77,'borrador')")
    with pytest.raises(sqlite3.IntegrityError):
        c.execute("insert into entrega(tarea_id,estudiante_id,estado) values (999,77,'borrador')")
    with pytest.raises(sqlite3.IntegrityError):
        c.execute("insert into entrega(tarea_id,estudiante_id,estado) values (3,77,'inventado')")
    assert "idx_entrega_estudiante" in {r["name"] for r in c.execute("select name from sqlite_master where type='index'")}
    # borrar al estudiante sigue borrando sus entregas (cascada)
    c.execute("delete from estudiante where id=77")
    assert [(r[0], r[1]) for r in c.execute("select tarea_id, estudiante_id from entrega")] == [(1, 78)]
    c.close()


def test_base_existente_sin_datos(ruta_bd):
    _base_version_2(ruta_bd, con_datos=False)
    init_db()
    c = get_connection()
    assert "estado" in _columnas(c, "entrega") and c.execute("select count(*) from entrega").fetchone()[0] == 0
    c.close()


def test_repetir_init_db_no_cambia_nada(ruta_bd):
    _base_version_2(ruta_bd)
    init_db()
    c = get_connection()
    antes = [tuple(r) for r in c.execute("select * from entrega order by 1,2")]
    c.close()
    init_db()
    init_db()
    c = get_connection()
    assert [tuple(r) for r in c.execute("select * from entrega order by 1,2")] == antes
    assert c.execute("select count(*) from schema_version where version=3").fetchone()[0] == 1
    c.close()


def test_si_la_migracion_falla_no_cambia_nada(ruta_bd):
    """Una fila huerfana (tarea inexistente) rompe la clave foranea al copiar: debe revertirse."""
    _base_version_2(ruta_bd)
    con = sqlite3.connect(ruta_bd)          # PRAGMA foreign_keys apagado por defecto: se cuela
    con.execute("INSERT INTO entrega(tarea_id,estudiante_id,entregada) VALUES (999,77,1)")
    con.commit()
    con.close()
    with pytest.raises(sqlite3.IntegrityError):
        init_db()
    c = get_connection()
    assert "entregada" in _columnas(c, "entrega"), "la tabla original debe quedar intacta"
    assert c.execute("select count(*) from entrega").fetchone()[0] == 4
    assert "entrega_nueva" not in {r["name"] for r in c.execute("select name from sqlite_master")}
    assert c.execute("select count(*) from schema_version where version=3").fetchone()[0] == 0
    c.close()
