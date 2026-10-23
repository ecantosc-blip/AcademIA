"""Las pruebas no pueden depender de cuentas, tokens ni red reales."""
import os
import socket

import pytest


def test_no_hay_credenciales_reales_en_el_entorno():
    for var in ("SGA_ACCESS_TOKEN", "SGA_REFRESH_TOKEN", "MOODLE_TOKEN", "MOODLE_PASSWORD"):
        assert var not in os.environ, var
    assert os.environ["ANTHROPIC_API_KEY"] == "clave-falsa-de-pruebas"


def test_dotenv_esta_desactivado():
    import dotenv
    assert dotenv.load_dotenv() is False


def test_la_red_esta_bloqueada():
    s = socket.socket()
    try:
        with pytest.raises(RuntimeError, match="Red bloqueada"):
            s.connect(("example.com", 443))
    finally:
        s.close()
