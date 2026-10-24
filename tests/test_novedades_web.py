"""Rutas de Novedades y datos que ve cada estudiante (sesion simulada)."""
from unittest import mock

from core import cambios
from db.setup import get_connection

SESION = {"estudiante_id": 77, "nombre": "ESTUDIANTE DE PRUEBA", "sga_access_token": "x",
          "sga_refresh_token": None, "moodle_token": "m"}


def _cliente(A, estudiante=77):
    sesion = dict(SESION, estudiante_id=estudiante)
    p = mock.patch.object(A, "get_session", return_value=("sid1", sesion))
    p.start()
    return A.app.test_client(), p


def _hacer_cambios(estudiante=77):
    c = get_connection()
    cambios.registrar(c, estudiante, "moodle")
    c.execute("insert into tarea(materia_id,moodle_assign_id,nombre,fecha_entrega) values (1,99,'Informe nuevo','2026-10-30T23:59:00')")
    cambios.registrar(c, estudiante, "moodle")
    c.commit()
    c.close()


def test_sin_sesion_da_401(app_web):
    with mock.patch.object(app_web, "get_session", return_value=(None, None)):
        cli = app_web.app.test_client()
        assert cli.get("/api/novedades").status_code == 401
        assert cli.post("/api/novedades/vistas").status_code == 401


def test_listar_y_marcar_como_vistas(app_web):
    _hacer_cambios()
    cli, p = _cliente(app_web)
    try:
        assert cli.get("/api/panel").get_json()["novedades_nuevas"] == 1
        d = cli.get("/api/novedades").get_json()
        assert [n["texto"] for n in d["nuevas"]] == ["Nueva tarea: Informe nuevo (vence 30 oct, 23:59)"]
        assert d["anteriores"] == []
        assert cli.post("/api/novedades/vistas").get_json() == {"marcadas": 1}
        d = cli.get("/api/novedades").get_json()
        assert d["nuevas"] == [] and len(d["anteriores"]) == 1
        assert cli.get("/api/panel").get_json()["novedades_nuevas"] == 0
        assert cli.post("/api/novedades/vistas").get_json() == {"marcadas": 0}
    finally:
        p.stop()


def test_cada_estudiante_solo_ve_sus_novedades(app_web):
    c = get_connection()
    c.execute("insert into estudiante(id,nombre) values (78,'OTRA PERSONA')")
    c.commit()
    c.close()
    _hacer_cambios(77)
    cli, p = _cliente(app_web, estudiante=78)
    try:
        assert cli.get("/api/novedades").get_json() == {"nuevas": [], "anteriores": []}
        assert cli.post("/api/novedades/vistas").get_json() == {"marcadas": 0}
    finally:
        p.stop()
    cli, p = _cliente(app_web, estudiante=77)
    try:
        assert len(cli.get("/api/novedades").get_json()["nuevas"]) == 1     # lo de 78 no borro lo de 77
    finally:
        p.stop()


def test_la_pagina_trae_la_vista_de_novedades(app_web):
    cli, p = _cliente(app_web)
    try:
        html = cli.get("/").get_data(as_text=True)
    finally:
        p.stop()
    assert 'id="v-novedades"' in html and 'data-vista="novedades"' in html and "/api/novedades" in html
