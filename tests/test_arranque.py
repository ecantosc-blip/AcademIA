"""
Los puntos de entrada deben arrancar como los ejecuta el usuario (`python web/app.py`),
SIN la configuracion de rutas que usa pytest. Un import de `core` antes de ajustar
sys.path solo falla en la maquina real, no en las pruebas normales.
"""
import os
import sys
import pathlib
import subprocess

import pytest

RAIZ = pathlib.Path(__file__).resolve().parents[1]

PROGRAMA = r"""
import os, sys, runpy
destino = sys.argv[1]
sys.path[0] = os.path.dirname(os.path.abspath(destino))     # como `python <script>`: solo la carpeta del script
runpy.run_path(destino, run_name="importado")
print("OK")
"""

SCRIPTS = ["web/app.py", "core/sync_all.py", "core/assistant.py", "core/query_engine.py", "db/setup.py",
           "moodle/client.py", "sga/client.py"] + \
          [f"herramientas/diagnostico/{n}" for n in ("debug_horario.py", "debug_malla.py", "debug_registro.py",
           "debug_materias_display.py", "debug_calificaciones.py", "debug_notas_moodle.py",
           "diagnostico_sga.py", "verificar_moodle.py")]


@pytest.mark.parametrize("script", SCRIPTS)
def test_se_puede_importar_como_lo_ejecuta_el_usuario(script, tmp_path):
    entorno = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH",)}
    entorno.update(DB_PATH=str(tmp_path / "arranque.db"), ANTHROPIC_API_KEY="falsa",
                   SESSION_ENC_KEY=os.environ["SESSION_ENC_KEY"])
    r = subprocess.run([sys.executable, "-I", "-c", PROGRAMA, str(RAIZ / script)], cwd=tmp_path,
                       env=entorno, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0 and "OK" in r.stdout, f"{script}\n{r.stderr[-800:]}"
