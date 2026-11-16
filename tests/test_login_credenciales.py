"""Login con usuario y contrasena de Moodle (ISO/IEC 27001: la contrasena se usa una vez y no se guarda).

Moodle y la sincronizacion van simulados: no se llama a ninguna red."""
import pytest


@pytest.fixture
def web(app_web, monkeypatch):
    A = app_web
    estado = {"login": [], "valido": True, "sync": 0, "sesion": None}

    class Moodle:
        def __init__(self, base_url=None, token=None):
            self.token = token

        def login(self, usuario, clave, service="moodle_mobile_app"):
            estado["login"].append((usuario, clave))
            if not estado["valido"]:
                raise ValueError("Invalid login")
            return "T" * 32

        def get_site_info(self):
            return {"userid": 77, "fullname": "Ana Prueba"}

    monkeypatch.setattr(A, "MoodleClient", Moodle)
    monkeypatch.setattr(A, "sync_estudiante", lambda **k: estado.__setitem__("sync", estado["sync"] + 1))
    monkeypatch.setattr(A, "avisos_de_sincronizacion", lambda *a: [])
    monkeypatch.setattr(A.sessions_store, "crear", lambda datos: estado.__setitem__("sesion", datos) or "sid1")
    return A, estado


def test_entra_con_usuario_y_clave_sin_erp(web):
    A, e = web
    r = A.app.test_client().post("/login", data={"moodle_usuario": "ana", "moodle_clave": "ClaveSecreta1"})
    assert r.status_code == 302
    assert e["login"] == [("ana", "ClaveSecreta1")] and e["sync"] == 1
    s = e["sesion"]
    assert s["moodle_token"] == "T" * 32 and s["sga_access_token"] == ""
    assert "ClaveSecreta1" not in repr(s), "la contrasena no debe quedar en la sesion"
    assert "solo con Moodle" in s["aviso_sync"]


def test_credenciales_incorrectas_no_repiten_lo_escrito(web):
    A, e = web
    e["valido"] = False
    r = A.app.test_client().post("/login", data={"moodle_usuario": "ana", "moodle_clave": "ClaveSecreta1"})
    t = r.get_data(as_text=True)
    assert r.status_code == 200 and "Usuario o contraseña incorrectos" in t
    assert "ClaveSecreta1" not in t and e["sesion"] is None


def test_faltan_datos(web):
    A, e = web
    t = A.app.test_client().post("/login", data={"moodle_usuario": "ana"}).get_data(as_text=True)
    assert "usuario y contraseña" in t and not e["login"]


def test_freno_por_usuario_contra_adivinar_claves(web, monkeypatch):
    A, e = web
    monkeypatch.setenv("LIMITE_LOGIN_USUARIO", "2/600")
    e["valido"] = False
    cl = A.app.test_client()
    codigos = [cl.post("/login", data={"moodle_usuario": "Ana", "moodle_clave": "x"}).status_code for _ in range(3)]
    assert codigos == [200, 200, 429]
    assert len(e["login"]) == 2, "el 3.er intento ni siquiera llega a Moodle"


def test_el_modo_token_avanzado_sigue_funcionando(web):
    import base64
    A, e = web
    tok = "moodlemobile://token=" + base64.b64encode(("1:::" + "a" * 32).encode()).decode()
    r = A.app.test_client().post("/login", data={"moodle_token": tok})
    assert r.status_code == 302 and not e["login"], "con token no se usa ninguna contrasena"


def test_la_pagina_de_login_pide_usuario_y_clave_y_el_erp_es_opcional():
    import app as A
    t = A.app.test_client().get("/login").get_data(as_text=True)
    assert 'name="moodle_usuario"' in t and 'type="password"' in t
    assert "(opcional)" in t and 'name="sga_access_token"' in t
