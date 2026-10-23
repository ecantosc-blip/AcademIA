"""Limites de uso: ventana deslizante, presupuesto diario, cupos y rutas."""
import time
import threading

import pytest

import limites
from ayudas import ClienteIAFalso
from core.estudio import Estudio


def test_ventana_deslizante():
    L = limites.Limitador()
    assert [L.permitir("k", 3, 10, ahora=t)[0] for t in (0, 1, 2, 3)] == [True, True, True, False]
    assert L.permitir("k", 3, 10, ahora=3)[1] >= 1
    assert L.permitir("k", 3, 10, ahora=11)[0]
    assert L.permitir("otra", 3, 10, ahora=3)[0], "otra clave no se ve afectada"


def test_presupuesto_diario(monkeypatch):
    monkeypatch.setenv("LIMITE_DIARIO_IA", "3")
    P = limites.PresupuestoIA()
    assert [P.consumir() for _ in range(4)] == [True, True, True, False]


def test_cupo_de_concurrencia(monkeypatch):
    monkeypatch.setenv("CUPO_TEST", "2")
    C = limites.Cupo("CUPO_TEST", 2, 1)
    res = []

    def tarea():
        try:
            with C.tomar(0.2):
                time.sleep(0.6)
                res.append("ok")
        except limites.Ocupado:
            res.append("ocupado")
    hs = [threading.Thread(target=tarea) for _ in range(5)]
    [h.start() for h in hs]
    [h.join() for h in hs]
    assert res.count("ok") == 2 and res.count("ocupado") == 3 and C.en_uso == 0 and C.rechazadas == 3


class AsistenteFalso:
    lock = threading.Lock()
    herramientas_usadas = []
    acciones = []

    def preguntar(self, q):
        return "hola"

    def close(self):
        pass


@pytest.fixture
def cliente_web(app_web, monkeypatch):
    A = app_web
    monkeypatch.setattr(A, "get_session", lambda: ("sidX", {"estudiante_id": 77, "nombre": "X", "sga_access_token": "",
                                                           "sga_refresh_token": "", "moodle_token": "t"}))
    A.ESTUDIOS["sidX"] = Estudio(77, client=ClienteIAFalso())
    A.ASSISTANTS["sidX"] = AsistenteFalso()
    return A, A.app.test_client()


def test_cabeceras_de_seguridad(cliente_web):
    A, cl = cliente_web
    h = cl.get("/estudio").headers
    assert h["X-Content-Type-Options"] == "nosniff" and h["X-Frame-Options"] == "DENY"
    assert "frame-ancestors 'none'" in h["Content-Security-Policy"] and h["Cache-Control"] == "no-store"


def test_limite_del_chat(cliente_web, monkeypatch):
    A, cl = cliente_web
    monkeypatch.setenv("LIMITE_CHAT", "3/60")
    limites.limitador.reiniciar()
    assert [cl.post("/api/chat", json={"pregunta": "hola"}).status_code for _ in range(5)] == [200, 200, 200, 429, 429]
    r = cl.post("/api/chat", json={"pregunta": "hola"})
    assert r.headers.get("Retry-After") and "Vas muy rápido" in r.get_json()["error"]


def test_presupuesto_diario_en_el_chat(cliente_web, monkeypatch):
    A, cl = cliente_web
    monkeypatch.setenv("LIMITE_CHAT", "100/60")
    monkeypatch.setenv("LIMITE_DIARIO_IA", "2")
    limites.limitador.reiniciar()
    limites.presupuesto_ia = limites.PresupuestoIA()
    A.limites.presupuesto_ia = limites.presupuesto_ia
    assert [cl.post("/api/chat", json={"pregunta": "hola"}).status_code for _ in range(3)] == [200, 200, 429]


def test_limite_del_estudio_y_responder_no_consume_ia(cliente_web, monkeypatch):
    A, cl = cliente_web
    monkeypatch.setenv("LIMITE_ESTUDIO", "2/60")
    limites.limitador.reiniciar()
    assert [cl.post("/api/estudio/practica", json={"materia_id": 1, "n": 3}).status_code for _ in range(3)] == [200, 200, 429]
    assert cl.post("/api/estudio/responder", json={"indice": 0, "opcion": 0}).status_code == 200


def test_sync_con_cupo_lleno_da_503(cliente_web, monkeypatch):
    A, cl = cliente_web
    monkeypatch.setenv("LIMITE_SYNC", "100/60")
    limites.limitador.reiniciar()
    for _ in range(limites.cupo_sync.n):
        limites.cupo_sync._sem.acquire()
    try:
        t0 = time.time()
        r = cl.post("/sync")
        assert r.status_code == 503 and time.time() - t0 >= 4
    finally:
        for _ in range(limites.cupo_sync.n):
            limites.cupo_sync._sem.release()


def test_login_limitado_por_ip(cliente_web, monkeypatch):
    A, cl = cliente_web
    monkeypatch.setenv("LIMITE_LOGIN", "2/600")
    limites.limitador.reiniciar()
    monkeypatch.setattr(A, "get_session", lambda: (None, None))
    assert [cl.post("/login", data={}).status_code for _ in range(3)] == [200, 200, 429]


def test_diccionarios_en_memoria_acotados(app_web, monkeypatch):
    A = app_web
    monkeypatch.setattr(A, "MAX_SESIONES_MEMORIA", 3)
    d = {}
    for i in range(6):
        A._guardar_acotado(d, f"s{i}", AsistenteFalso())
    assert list(d) == ["s3", "s4", "s5"]
