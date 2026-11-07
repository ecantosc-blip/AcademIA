"""Recordatorios por Telegram: vinculo con codigo, avisos a la hora, sin repetir, /stop y privacidad."""
from unittest import mock

import pytest

from core import agenda_personal as AP
from core import telegram as T
from core.trabajador import Trabajador
from db.setup import get_connection

EST = 77
LUNES_8 = "2030-01-07T08:00:00"      # un lunes


@pytest.fixture
def conn(app_web):
    c = get_connection()
    c.execute("DELETE FROM tarea"); c.execute("DELETE FROM evento")
    c.execute("INSERT INTO tarea(materia_id, moodle_assign_id, nombre, fecha_entrega) VALUES (1, 900, 'Informe final', '2030-01-09T23:00:00')")
    c.execute("INSERT INTO entrega(tarea_id, estudiante_id, estado, verificada_en) SELECT id, 77, 'sin_entregar', 'x' FROM tarea WHERE moodle_assign_id = 900")
    c.commit()
    AP.guardar_franjas(c, EST, [{"dia": "lunes", "inicio": "09:00", "fin": "11:00"}])
    yield c
    c.close()


def _vincular(c, chat="555"):
    codigo = T.crear_codigo(c, EST, ahora=LUNES_8)
    assert T.responder(c, {"chat": {"id": int(chat), "type": "private"}, "text": f"/start {codigo}"}, ahora=LUNES_8)
    return codigo


# ----------------------------------------------------------------- vinculo
def test_el_codigo_se_guarda_como_hash_vence_y_se_usa_una_vez(conn):
    codigo = T.crear_codigo(conn, EST, ahora=LUNES_8)
    fila = conn.execute("SELECT * FROM telegram_vinculo WHERE estudiante_id=?", (EST,)).fetchone()
    assert codigo not in str(tuple(fila)) and len(fila["codigo_hash"]) == 64          # solo el hash
    assert T.canjear_codigo(conn, codigo, 555, ahora="2030-01-07T08:11:00") is None  # vencido (10 min)
    assert T.canjear_codigo(conn, "otro", 555, ahora=LUNES_8) is None
    assert T.canjear_codigo(conn, codigo, 555, ahora="2030-01-07T08:05:00") == EST
    assert T.canjear_codigo(conn, codigo, 555, ahora="2030-01-07T08:06:00") is None  # un solo uso
    assert T.estado(conn, EST) == {"vinculado": True, "activo": True, "avisar_min": 10, "vinculado_en": "2030-01-07T08:05:00"}


def test_un_chat_pertenece_a_una_sola_persona(conn):
    conn.execute("INSERT INTO estudiante(id, nombre) VALUES (78, 'OTRA')"); conn.commit()
    _vincular(conn, "555")
    codigo = T.crear_codigo(conn, 78, ahora=LUNES_8)
    T.canjear_codigo(conn, codigo, 555, ahora=LUNES_8)
    assert not T.estado(conn, EST)["vinculado"] and T.estado(conn, 78)["vinculado"]


def test_el_bot_solo_responde_en_chats_privados_y_con_comandos_conocidos(conn):
    assert T.responder(conn, {"chat": {"id": 1, "type": "group"}, "text": "/start abc"}) is None
    assert T.responder(conn, {"chat": {"id": 1, "type": "private"}, "text": "hola"})[1] == T.AYUDA
    assert "no sirve" in T.responder(conn, {"chat": {"id": 1, "type": "private"}, "text": "/start mal"})[1]
    assert T.responder(conn, None) is None
    _vincular(conn)
    assert "borré el vínculo" in T.responder(conn, {"chat": {"id": 555, "type": "private"}, "text": "/stop"})[1]
    assert not T.estado(conn, EST)["vinculado"]


def test_preferencias_validan_y_exigen_vinculo(conn):
    with pytest.raises(ValueError):
        T.guardar_preferencias(conn, EST, avisar_min=10)             # sin vinculo
    _vincular(conn)
    T.guardar_preferencias(conn, EST, activo=False, avisar_min=30)
    e = T.estado(conn, EST)
    assert e["avisar_min"] == 30 and e["activo"] is False and e["vinculado"] is True
    for malo in (7, True, "10"):
        with pytest.raises(ValueError):
            T.guardar_preferencias(conn, EST, avisar_min=malo)


# ----------------------------------------------------------------- recordatorios
def test_avisa_unos_minutos_antes_una_sola_vez(conn):
    _vincular(conn)                       # avisar_min = 10; el plan pone el bloque a las 09:00
    assert T.pendientes(conn, "2030-01-07T08:40:00") == []                      # falta mucho
    env = mock.Mock()
    assert T.enviar_pendientes(conn, "TOK", "2030-01-07T08:52:00", enviar_fn=env) == 1
    chat, texto = env.call_args.args[1], env.call_args.args[2]
    assert chat == "555" and "09:00" in texto and "Informe final" in texto and "60 min" in texto
    assert "/stop" in texto and "TOK" not in texto
    assert T.enviar_pendientes(conn, "TOK", "2030-01-07T08:55:00", enviar_fn=env) == 0   # no se repite
    assert env.call_count == 1


def test_no_avisa_si_esta_pausado_ni_si_ya_paso_mucho(conn):
    _vincular(conn)
    T.guardar_preferencias(conn, EST, activo=False)
    assert T.pendientes(conn, "2030-01-07T08:55:00") == []
    T.guardar_preferencias(conn, EST, activo=True)
    assert T.pendientes(conn, "2030-01-07T09:30:00") == []                      # pasaron 30 min
    assert len(T.pendientes(conn, "2030-01-07T09:05:00")) == 1                  # atraso tolerado (<=10 min)


def test_si_telegram_falla_no_se_marca_enviado_y_si_bloquearon_al_bot_se_desvincula(conn):
    _vincular(conn)
    falla = mock.Mock(side_effect=T.TelegramError("error de red con Telegram"))
    assert T.enviar_pendientes(conn, "TOK", "2030-01-07T08:55:00", enviar_fn=falla) == 0
    assert conn.execute("SELECT COUNT(*) FROM recordatorio_enviado").fetchone()[0] == 0
    assert T.estado(conn, EST)["vinculado"]                                    # error de red: se reintenta
    bloqueado = mock.Mock(side_effect=T.TelegramError("Telegram rechazó la petición (403)", 403))
    T.enviar_pendientes(conn, "TOK", "2030-01-07T08:55:00", enviar_fn=bloqueado)
    assert not T.estado(conn, EST)["vinculado"]


def test_los_avisos_viejos_se_limpian(conn):
    conn.execute("INSERT INTO recordatorio_enviado VALUES (77, 'viejo', '2030-01-01T10:00:00')"); conn.commit()
    T.enviar_pendientes(conn, "TOK", LUNES_8, enviar_fn=mock.Mock())
    assert conn.execute("SELECT COUNT(*) FROM recordatorio_enviado WHERE clave='viejo'").fetchone()[0] == 0


# ----------------------------------------------------------------- API y trabajador
def test_la_api_de_telegram_no_filtra_el_token_en_los_errores():
    import requests
    with mock.patch.object(T.requests, "post", side_effect=requests.ConnectionError("fallo en https://api.telegram.org/botSECRETO/sendMessage")):
        with pytest.raises(T.TelegramError) as e:
            T.enviar("SECRETO", 1, "hola")
    assert "SECRETO" not in str(e.value) and e.value.__cause__ is None


def test_el_trabajador_responde_al_start_y_envia_avisos(conn, monkeypatch):
    codigo = T.crear_codigo(conn, EST)               # con la hora real
    enviados = []
    monkeypatch.setattr(T, "actualizaciones", lambda tok, off, esp: [
        {"update_id": 5, "message": {"chat": {"id": 42, "type": "private"}, "text": f"/start {codigo}"}}])
    monkeypatch.setattr(T, "enviar", lambda tok, chat, txt: enviados.append((chat, txt)))
    w = Trabajador("TOK")
    w.un_ciclo(espera=0)
    assert w.offset == 6 and T.estado(conn, EST)["vinculado"]
    assert enviados and "quedaste conectado" in enviados[0][1]


def test_rutas_web_de_telegram(app_web, monkeypatch):
    from unittest import mock as m
    sesion = {"estudiante_id": EST, "nombre": "X", "moodle_token": "m", "sga_access_token": "x", "sga_refresh_token": None}
    with m.patch.object(app_web, "get_session", return_value=("s", dict(sesion))):
        c = app_web.app.test_client()
        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
        assert c.post("/api/telegram/vincular").status_code == 400
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "TOK"); monkeypatch.setenv("TELEGRAM_BOT_USERNAME", "@mi_bot")
        d = c.post("/api/telegram/vincular").get_json()
        assert d["enlace"].startswith("https://t.me/mi_bot?start=") and d["vence_min"] == 10
        assert c.post("/api/telegram/preferencias", json={"avisar_min": 10}).status_code == 400   # aun sin chat
        plan = c.get("/api/plan").get_json()
        assert plan["telegram"]["configurado"] is True and plan["telegram"]["vinculado"] is False and "TOK" not in str(plan)
        assert c.post("/api/telegram/desvincular").get_json()["vinculado"] is False
        r = c.post("/api/agenda", json={"franjas": [{"dia": "sabado", "inicio": "09:00", "fin": "11:00"}],
                                        "actividades": []}).get_json()
        assert [{k: v for k, v in f.items() if k != "id"} for f in r["agenda"]["franjas"]] == [{"dia": "sabado", "inicio": "09:00", "fin": "11:00"}]
        assert c.post("/api/agenda", json={"franjas": [{"dia": "x", "inicio": "1", "fin": "2"}]}).status_code == 400


def test_mis_datos_incluye_lo_nuevo_y_borra_todo_en_cascada(conn):
    import mis_datos
    _vincular(conn)
    ex = mis_datos.exportar(EST)
    assert ex["franjas_libres"] and ex["telegram"] and "chat_id" not in str(ex["telegram"])
    nombres = [x["nombre"] for x in mis_datos.resumen(EST)["conteos"]]
    assert "Telegram conectado para recordatorios" in nombres
    mis_datos.borrar(EST)
    c = get_connection()
    try:
        for t in ("franja_libre", "actividad", "telegram_vinculo", "recordatorio_enviado", "bloque_fijo"):
            assert c.execute(f"SELECT COUNT(*) FROM {t} WHERE estudiante_id=77").fetchone()[0] == 0
    finally:
        c.close()


def test_un_bloque_no_se_vuelve_a_avisar_con_otra_hora_cuando_pasa_el_tiempo(conn):
    _vincular(conn)
    env = mock.Mock()
    assert T.enviar_pendientes(conn, "TOK", "2030-01-07T08:52:00", enviar_fn=env) == 1
    # a las 09:05 el plan "normal" recortaria el bloque a 09:05; el aviso usa el plan estable del dia
    assert T.enviar_pendientes(conn, "TOK", "2030-01-07T09:05:00", enviar_fn=env) == 0
    assert env.call_count == 1


def test_el_usuario_del_bot_se_pregunta_a_telegram_y_los_errores_se_explican(app_web, monkeypatch):
    T._estado["usuario_bot"] = None
    with mock.patch.object(T, "_llamar", return_value={"username": "UnemIABot"}) as ll:
        assert T.usuario_bot(None, "TOK") == "UnemIABot" and ll.call_args.args[1] == "getMe"
    T._estado["usuario_bot"] = None
    sesion = {"estudiante_id": EST, "nombre": "X", "moodle_token": "m", "sga_access_token": "x", "sga_refresh_token": None}
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:SECRETO"); monkeypatch.delenv("TELEGRAM_BOT_USERNAME", raising=False)
    with mock.patch.object(app_web, "get_session", return_value=("s", sesion)), \
         mock.patch.object(T, "_llamar", side_effect=T.TelegramError("Telegram rechazó la petición (401)", 401)):
        r = app_web.app.test_client().post("/api/telegram/vincular")
    assert r.status_code == 400 and "401" in r.get_json()["error"] and "SECRETO" not in r.get_data(as_text=True)
    T._estado["usuario_bot"] = None
