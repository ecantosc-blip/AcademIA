"""
Configuracion comun de las pruebas.

Garantias (se comprueban en tests/test_aislamiento.py):
  * No se usan cuentas, tokens ni claves reales: se descartan las variables de
    entorno sensibles y se desactiva la lectura de .env.
  * No hay red: cualquier intento de abrir un socket falla la prueba.
  * Cada prueba trabaja con su propia base SQLite temporal.
"""
import os
import sys
import socket
import tempfile
import pathlib

import pytest

RAIZ = pathlib.Path(__file__).resolve().parents[1]
for ruta in (str(RAIZ), str(RAIZ / "web")):
    if ruta not in sys.path:
        sys.path.insert(0, ruta)

# --- Entorno aislado (antes de importar cualquier modulo del proyecto) -------
_VARIABLES_SENSIBLES = (
    "SGA_ACCESS_TOKEN", "SGA_REFRESH_TOKEN", "SGA_ALUMNO_ID", "SGA_BASE_URL",
    "MOODLE_TOKEN", "MOODLE_USERNAME", "MOODLE_PASSWORD", "MOODLE_BASE_URL",
    "MOODLE_SERVICE", "ANTHROPIC_API_KEY", "SESSION_ENC_KEY",
)
for _v in _VARIABLES_SENSIBLES:
    os.environ.pop(_v, None)

import dotenv  # noqa: E402
dotenv.load_dotenv = lambda *a, **k: False   # un .env real nunca se lee en pruebas

from cryptography.fernet import Fernet  # noqa: E402

_TMP = tempfile.mkdtemp(prefix="academia_pruebas_")
os.environ["DB_PATH"] = os.path.join(_TMP, "inicial.db")
os.environ["ANTHROPIC_API_KEY"] = "clave-falsa-de-pruebas"
os.environ["SESSION_ENC_KEY"] = Fernet.generate_key().decode()   # no escribe .session_key


@pytest.fixture(autouse=True)
def sin_red(monkeypatch):
    """Bloquea la red: las pruebas solo pueden usar datos simulados."""
    def _bloqueado(self, *args, **kwargs):
        raise RuntimeError("Red bloqueada en las pruebas: usa datos simulados")
    monkeypatch.setattr(socket.socket, "connect", _bloqueado)
    monkeypatch.setattr(socket.socket, "connect_ex", _bloqueado)


@pytest.fixture
def ruta_bd(tmp_path, monkeypatch):
    """Ruta de una base nueva (todavia sin crear) para esta prueba."""
    ruta = str(tmp_path / "prueba.db")
    monkeypatch.setenv("DB_PATH", ruta)
    return ruta


@pytest.fixture
def bd_vacia(ruta_bd):
    """Base nueva con el esquema completo y sin datos."""
    from db.setup import init_db
    init_db()
    return ruta_bd


@pytest.fixture
def bd_demo(bd_vacia):
    """Base con un estudiante simulado (id 77) y datos de ejemplo."""
    import ayudas
    ayudas.sembrar_demo(bd_vacia)
    return bd_vacia


@pytest.fixture
def app_web(bd_demo, monkeypatch):
    """Modulo web/app.py con limites reiniciados y la base demo."""
    import limites
    import app as A
    limites.limitador.reiniciar()
    monkeypatch.setenv("LIMITE_DIARIO_IA", "1000")
    limites.presupuesto_ia = limites.PresupuestoIA()
    A.limites.presupuesto_ia = limites.presupuesto_ia
    A.ESTUDIOS.clear()
    A.ASSISTANTS.clear()
    return A
