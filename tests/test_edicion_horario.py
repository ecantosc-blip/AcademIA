"""Edicion del horario: rutina (agregar/editar/borrar), bloques fijados y movimiento seguro."""
from datetime import date, timedelta
from unittest import mock

import pytest

from core import agenda_personal as AP
from core import bloques_fijos as BF
from core import planificador
from db.setup import get_connection

EST = 77
AHORA = "2030-01-07T08:00:00"          # lunes
SESION = {"estudiante_id": EST, "nombre": "X", "moodle_token": "m", "sga_access_token": "x", "sga_refresh_token": None}


@pytest.fixture
def conn(app_web):
    c = get_connection()
    c.execute("DELETE FROM tarea"); c.execute("DELETE FROM evento")
    for i, (n, f) in enumerate((("Tarea A", "2030-01-10T23:00:00"), ("Tarea B", "2030-01-11T23:00:00")), 800):
        c.execute("INSERT INTO tarea(materia_id, moodle_assign_id, nombre, fecha_entrega) VALUES (1,?,?,?)", (i, n, f))
        c.execute("INSERT INTO entrega(tarea_id, estudiante_id, estado, verificada_en) SELECT id,77,'sin_entregar','x' FROM tarea WHERE moodle_assign_id=?", (i,))
    c.commit()
    AP.guardar_franjas(c, EST, [{"dia": d, "inicio": "08:00", "fin": "12:00"} for d in ("lunes", "martes", "miercoles", "jueves", "viernes")])
    yield c
    c.close()


def _bloques(c, ahora=AHORA):
    p = planificador.generar(c, EST, ahora=ahora)
    return [(d["fecha"], b) for d in p["dias"] for b in d["bloques"]]


# ----------------------------------------------------------------- rutina
def test_agregar_editar_y_borrar_rutina_sin_perder_lo_demas(conn):
    AP.agregar(conn, EST, "actividad", [{"dia": "lunes", "inicio": "07:00", "fin": "07:30", "nombre": "Desayuno"}])
    AP.agregar(conn, EST, "actividad", [{"dia": "martes", "inicio": "13:00", "fin": "14:00", "nombre": "Almuerzo"}])
    acts = AP.listar(conn, EST)["actividades"]
    assert [a["nombre"] for a in acts] == ["Desayuno", "Almuerzo"] and all("id" in a for a in acts)
    AP.editar(conn, EST, "actividad", acts[0]["id"], {"dia": "lunes", "inicio": "07:30", "fin": "08:00", "nombre": "Desayuno"})
    assert AP.listar(conn, EST)["actividades"][0]["inicio"] == "07:30"
    AP.borrar(conn, EST, "actividad", AP.listar(conn, EST)["actividades"][0]["id"])
    assert [a["nombre"] for a in AP.listar(conn, EST)["actividades"]] == ["Almuerzo"]
    with pytest.raises(ValueError):
        AP.borrar(conn, EST, "actividad", 99999)
    with pytest.raises(ValueError):
        AP.agregar(conn, EST, "otra", [])
    with pytest.raises(ValueError):                                   # un error no toca lo guardado
        AP.agregar(conn, EST, "actividad", [{"dia": "lunes", "inicio": "10:00", "fin": "09:00", "nombre": "x"}])
    assert len(AP.listar(conn, EST)["actividades"]) == 1


def test_agregar_franja_se_une_con_la_vecina(conn):
    AP.agregar(conn, EST, "franja", [{"dia": "lunes", "inicio": "11:00", "fin": "14:00"}])
    lunes = [f for f in AP.listar(conn, EST)["franjas"] if f["dia"] == "lunes"]
    assert [(f["inicio"], f["fin"]) for f in lunes] == [("08:00", "14:00")]


# ----------------------------------------------------------------- mover y fijar
def test_mover_fija_el_bloque_y_el_plan_lo_respeta(conn):
    fecha, b = next((f, x) for f, x in _bloques(conn) if x["clave"].startswith("t:"))
    destino = (date.fromisoformat(fecha) + timedelta(days=1)).isoformat()
    BF.mover(conn, EST, b["clave"], fecha, b["inicio"], destino, "10:30", ahora=AHORA)
    fijados = [(f, x) for f, x in _bloques(conn) if x.get("fijado")]
    assert len(fijados) == 1 and fijados[0][0] == destino and fijados[0][1]["inicio"] == "10:30"
    assert fijados[0][1]["minutos"] == b["minutos"] and "Lo fijaste tú" in fijados[0][1]["motivo"]
    # el resto se reacomoda alrededor y nada se solapa
    for f in {f for f, _ in _bloques(conn)}:
        dia = sorted((x["inicio"], x["fin"]) for g, x in _bloques(conn) if g == f)
        assert all(a[1] <= b2[0] for a, b2 in zip(dia, dia[1:]))
    assert BF.soltar(conn, EST) == 1
    assert not any(x.get("fijado") for _, x in _bloques(conn))


def test_mover_valida_dia_hora_pasado_y_choques(conn):
    fecha, b = next((f, x) for f, x in _bloques(conn) if x["clave"].startswith("t:"))
    c = dict(clave=b["clave"], desde_fecha=fecha, desde_inicio=b["inicio"], ahora=AHORA)
    with pytest.raises(ValueError, match="próximos 7 días"):
        BF.mover(conn, EST, fecha="2030-02-01", inicio="09:00", **c)
    with pytest.raises(ValueError, match="entre las 07:00 y las 24:00"):
        BF.mover(conn, EST, fecha=fecha, inicio="06:00", **c)
    with pytest.raises(ValueError, match="entre las 07:00 y las 24:00"):
        BF.mover(conn, EST, fecha=fecha, inicio="23:30", **c)
    tarde = "2030-01-07T09:00:00"
    f9, b9 = next((f, x) for f, x in _bloques(conn, tarde) if x["clave"].startswith("t:"))
    with pytest.raises(ValueError, match="Esa hora ya pasó"):
        BF.mover(conn, EST, b9["clave"], f9, b9["inicio"], "2030-01-07", "07:30", ahora=tarde)
    with pytest.raises(ValueError, match="no es una hora válida"):
        BF.mover(conn, EST, fecha=fecha, inicio="abc", **c)
    with pytest.raises(ValueError, match="Choca con «SISTEMA DE AUTOMATIZACIÓN INDUSTRIAL»"):
        BF.mover(conn, EST, fecha="2030-01-07", inicio="18:30", **c)          # lunes 18-20 hay clase
    with pytest.raises(ValueError, match="ya no está en el plan"):
        BF.mover(conn, EST, "t:999999", fecha, "09:00", fecha, "10:00", ahora=AHORA)
    assert BF.listar(conn, EST) == []                                          # nada quedo guardado


def test_dos_bloques_fijados_no_pueden_chocar(conn):
    bl = [(f, x) for f, x in _bloques(conn) if x["clave"].startswith("t:")]
    (f1, b1), (f2, b2) = bl[0], next(t for t in bl[1:] if t[1]["clave"] != bl[0][1]["clave"])
    BF.mover(conn, EST, b1["clave"], f1, b1["inicio"], "2030-01-08", "10:00", ahora=AHORA)
    bl2 = [(f, x) for f, x in _bloques(conn) if x["clave"] == b2["clave"] and not x.get("fijado")]
    f2, b2 = bl2[0]
    with pytest.raises(ValueError, match="otro bloque que ya fijaste"):
        BF.mover(conn, EST, b2["clave"], f2, b2["inicio"], "2030-01-08", "10:30", ahora=AHORA)


def test_un_fijado_de_algo_que_ya_no_esta_en_el_plan_se_ignora(conn):
    conn.execute("INSERT INTO bloque_fijo(estudiante_id, clave, fecha, inicio, fin) VALUES (77,'t:424242','2030-01-08','09:00','10:00')")
    conn.commit()
    assert not any(x["clave"] == "t:424242" for _, x in _bloques(conn))


# ----------------------------------------------------------------- rutas web
def test_rutas_web_de_edicion(app_web):
    with mock.patch.object(app_web, "get_session", return_value=("s", dict(SESION))):
        c = app_web.app.test_client()
        r = c.post("/api/agenda/agregar", json={"actividades": [{"dia": "lunes", "inicio": "07:00", "fin": "07:30", "nombre": "Desayuno"}]})
        assert r.status_code == 200
        act = r.get_json()["agenda"]["actividades"][0]
        assert c.post("/api/agenda/editar", json={"tipo": "actividad", "id": act["id"],
                      "item": {"dia": "lunes", "inicio": "07:15", "fin": "07:45", "nombre": "Desayuno"}}).status_code == 200
        assert c.post("/api/agenda/borrar", json={"tipo": "actividad", "id": 12345}).status_code == 400
        assert c.post("/api/agenda/agregar", json={"actividades": [{"dia": "x"}]}).status_code == 400
        assert c.post("/api/plan/mover", json={"clave": "t:1"}).status_code == 400
        assert c.post("/api/plan/soltar", json={}).status_code == 200
        borrado = c.post("/api/agenda/borrar", json={"tipo": "actividad", "id": c.get("/api/plan").get_json()["agenda"]["actividades"][0]["id"]})
        assert borrado.get_json()["agenda"]["actividades"] == []
    with mock.patch.object(app_web, "get_session", return_value=(None, None)):
        c = app_web.app.test_client()
        for ruta in ("/api/agenda/agregar", "/api/agenda/editar", "/api/agenda/borrar", "/api/plan/mover", "/api/plan/soltar"):
            assert c.post(ruta, json={}).status_code == 401
