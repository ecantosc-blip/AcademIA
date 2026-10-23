"""Renovacion del token del ERP (access + refresh) con respuestas simuladas."""
import time
import base64

import pytest
import requests

from ayudas import jwt
from sga.client import SGAClient, RUTA_REFRESH

BASE = "https://sga.unemi.edu.ec"


class Resp:
    def __init__(self, code, data=None):
        self.status_code = code
        self._d = data

    def json(self):
        if self._d is None:
            raise ValueError("no json")
        return self._d

    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception(f"HTTP {self.status_code}")


class Ses:
    def __init__(self, guion):
        self.guion = list(guion)
        self.calls = []

    def post(self, url, headers=None, json=None, params=None, timeout=None):
        self.calls.append(("POST", url, dict(headers or {}), json))
        return self.guion.pop(0)

    def get(self, url, headers=None, params=None, timeout=None):
        self.calls.append(("GET", url, dict(headers or {}), None))
        return self.guion.pop(0)


@pytest.fixture
def tokens():
    return jwt(int(time.time()) - 100), jwt(int(time.time()) + 7200)


def cliente(acceso, refresco, guion):
    c = SGAClient(BASE, acceso, refresco)
    c.session = Ses(guion)
    return c


def test_renueva_y_conserva_refresh(tokens):
    viejo, nuevo = tokens
    c = cliente(viejo, "R1", [Resp(200, {"access": nuevo})])
    assert c.refresh_access_token() and c.access_token == nuevo and c.refresh_token == "R1"
    m, u, h, b = c.session.calls[0]
    assert u == BASE + RUTA_REFRESH and m == "POST" and b == {"refresh": "R1"}
    assert h["Authorization"] == "Bearer " + viejo


def test_refresh_rotado_se_guarda(tokens):
    viejo, nuevo = tokens
    c = cliente(viejo, "R1", [Resp(200, {"access": nuevo, "refresh": "R2"})])
    c.refresh_access_token()
    assert c.refresh_token == "R2"


def test_rechazo_con_cabecera_reintenta_sin_ella(tokens):
    viejo, nuevo = tokens
    c = cliente(viejo, "R1", [Resp(401, {}), Resp(200, {"access": nuevo})])
    assert c.refresh_access_token() and "Authorization" not in c.session.calls[1][2]


def test_refresh_invalido_no_toca_el_access(tokens):
    viejo, _ = tokens
    c = cliente(viejo, "R1", [Resp(401, {}), Resp(401, {})])
    assert not c.refresh_access_token() and c.access_token == viejo


def test_casos_borde(tokens):
    viejo, _ = tokens
    c = cliente(viejo, None, [])
    assert not c.refresh_access_token() and not c.session.calls
    assert not cliente(viejo, "R1", [Resp(200, None)]).refresh_access_token()
    assert not cliente(viejo, "R1", [Resp(200, {"x": 1})]).refresh_access_token()

    class Boom:
        def post(self, *a, **k):
            raise requests.ConnectionError("sin red")
    c = SGAClient(BASE, viejo, "R1")
    c.session = Boom()
    assert not c.refresh_access_token()


def test_renueva_antes_de_la_peticion_si_falta_poco(tokens):
    _, nuevo = tokens
    c = cliente(jwt(int(time.time()) + 20), "R1", [Resp(200, {"access": nuevo}), Resp(200, {"ok": 1})])
    c._get("/api/x")
    assert c.session.calls[0][1].endswith(RUTA_REFRESH)
    assert c.session.calls[1][2]["Authorization"] == "Bearer " + nuevo


def test_no_renueva_con_tiempo_de_sobra():
    c = cliente(jwt(int(time.time()) + 5000), "R1", [Resp(200, {"ok": 1})])
    c._get("/api/x")
    assert len(c.session.calls) == 1


def test_401_del_servidor_renueva_y_reintenta(tokens):
    _, nuevo = tokens
    c = cliente(jwt(int(time.time()) + 5000), "R1", [Resp(401, {}), Resp(200, {"access": nuevo}), Resp(200, {"ok": 2})])
    assert c._get("/api/x") == {"ok": 2}


def test_401_sin_refresh_da_mensaje_claro():
    c = cliente(jwt(int(time.time()) + 5000), None, [Resp(401, {})])
    with pytest.raises(PermissionError, match="Token expirado"):
        c._get("/api/x")


# --- Integracion con la app web ---------------------------------------------
@pytest.fixture
def web_con_erp_falso(app_web, monkeypatch, tokens):
    _, nuevo = tokens
    A = app_web
    estado = {"llamadas": 0, "ok": True}
    guardadas = []
    monkeypatch.setattr(A.sessions_store, "guardar", lambda sid, s: guardadas.append(dict(s)))

    class ERPFalso(SGAClient):
        def refresh_access_token(self):
            estado["llamadas"] += 1
            if estado["ok"]:
                self.access_token = nuevo
                return True
            return False
    monkeypatch.setattr(A, "SGAClient", ERPFalso)
    return A, estado, guardadas, nuevo


def test_panel_renueva_y_guarda(web_con_erp_falso, monkeypatch):
    A, estado, guardadas, nuevo = web_con_erp_falso
    ses = {"estudiante_id": 77, "nombre": "X", "sga_access_token": jwt(int(time.time()) + 100),
           "sga_refresh_token": "R1", "moodle_token": "t"}
    monkeypatch.setattr(A, "get_session", lambda: ("sid1", ses))
    cl = A.app.test_client()
    d = cl.get("/api/panel").get_json()
    assert estado["llamadas"] == 1 and ses["sga_access_token"] == nuevo and guardadas
    assert d["conexiones"]["erp"]["renovable"] is True and d["conexiones"]["erp"]["vence"] > time.time() + 7000
    cl.get("/api/panel")
    assert estado["llamadas"] == 1, "token fresco: no vuelve a renovar"
    ses["sga_access_token"] = jwt(int(time.time()) + 100)
    estado["ok"] = False
    ses.pop("sga_renov_intento", None)
    for _ in range(3):
        cl.get("/api/panel")
    assert estado["llamadas"] == 2, "si falla, un solo intento cada 5 min"
    ses["sga_refresh_token"] = ""
    ses["sga_renov_intento"] = 0
    n = estado["llamadas"]
    cl.get("/api/panel")
    assert estado["llamadas"] == n, "sin refresh no intenta"


def test_login_con_access_vencido(web_con_erp_falso, monkeypatch):
    A, estado, guardadas, nuevo = web_con_erp_falso

    class Moodle:
        def __init__(self, **k):
            pass

        def get_site_info(self):
            return {"userid": 77, "fullname": "X"}
    monkeypatch.setattr(A, "MoodleClient", Moodle)
    monkeypatch.setattr(A, "sync_estudiante", lambda **k: None)
    monkeypatch.setattr(A, "get_session", lambda: (None, None))
    mt = "moodlemobile://token=" + base64.b64encode(("1:::" + "a" * 32).encode()).decode()
    cl = A.app.test_client()
    r = cl.post("/login", data={"moodle_token": mt, "sga_access_token": jwt(int(time.time()) - 500), "sga_refresh_token": ""})
    assert "ya venció" in r.get_data(as_text=True), "access vencido sin refresh -> rechazado"
    estado["llamadas"] = 0
    r = cl.post("/login", data={"moodle_token": mt, "sga_access_token": jwt(int(time.time()) - 500), "sga_refresh_token": "R1"})
    assert estado["llamadas"] == 1 and r.status_code == 302
