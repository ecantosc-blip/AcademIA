"""Descarga del carne: ruta /carnet y flujo desde el chat (todo simulado)."""
import types
from unittest import mock

import pytest

SESION = {"estudiante_id": 77, "sga_access_token": "x", "sga_refresh_token": None, "moodle_token": "m"}


class RespPDF:
    content = b"%PDF-1.4 fake"
    headers = {"Content-Type": "application/pdf"}

    def raise_for_status(self):
        pass


def _pedir(A, info):
    with mock.patch.object(A, "get_session", return_value=("sid1", dict(SESION))), \
         mock.patch.object(A.SGAClient, "cambiar_a_perfil_regular", return_value=False), \
         mock.patch.object(A.SGAClient, "get_carnet_parseado", return_value=info), \
         mock.patch.object(A.requests, "get", return_value=RespPDF()), \
         mock.patch.object(A.sessions_store, "guardar"):
        return A.app.test_client().get("/carnet")


def test_carnet_ok(app_web):
    r = _pedir(app_web, {"pdf_url": "https://sga.unemi.edu.ec/media/carnet/x.pdf"})
    assert r.status_code == 200 and r.headers["Content-Type"] == "application/pdf"
    assert "carne-unemi.pdf" in r.headers["Content-Disposition"] and r.data.startswith(b"%PDF")


def test_carnet_inexistente_da_404(app_web):
    assert _pedir(app_web, {"pdf_url": None}).status_code == 404


def test_carnet_de_dominio_no_permitido_da_400(app_web):
    assert _pedir(app_web, {"pdf_url": "https://malo.com/x.pdf"}).status_code == 400


def test_carnet_sin_sesion_da_401(app_web):
    with mock.patch.object(app_web, "get_session", return_value=(None, None)):
        assert app_web.app.test_client().get("/carnet").status_code == 401


def test_chat_devuelve_accion_de_carnet(app_web):
    A = app_web
    from core import assistant as AS
    B = lambda **k: types.SimpleNamespace(**k)
    secuencia = [B(stop_reason="tool_use", content=[B(type="tool_use", name="carnet_estudiantil", input={}, id="t1")]),
                 B(stop_reason="end_turn", content=[B(type="text", text="Listo, pulsa el botón para bajar tu carné.")])]

    class Falso:
        def __init__(self):
            self.messages = self

        def create(self, **k):
            return secuencia.pop(0)
    with mock.patch.object(AS.anthropic, "Anthropic", return_value=Falso()):
        a = AS.AssistantLLM(estudiante_id=77)
    with mock.patch.object(A, "get_session", return_value=("s1", {"estudiante_id": 77})), \
         mock.patch.object(A, "get_asistente_para", return_value=a):
        r = A.app.test_client().post("/api/chat", json={"pregunta": "quiero mi carné"})
    assert r.status_code == 200 and r.get_json()["acciones"] == ["carnet"]
