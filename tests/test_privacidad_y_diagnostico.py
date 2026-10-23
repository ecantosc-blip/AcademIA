"""
Privacidad: tokens y datos personales no deben salir en errores, registros ni en la
salida de los scripts de diagnostico (probado con servicios simulados).
"""
import ast
import json
import pathlib
import importlib
import subprocess

import pytest
import requests

from ayudas import jwt
from core import privacidad
from core.privacidad import depurar, enmascarar, guardar_local, resumir_estructura, Alias

RAIZ = pathlib.Path(__file__).resolve().parents[1]
DIAG = RAIZ / "herramientas" / "diagnostico"

TOKEN_MOODLE = "a1b2c3d4e5f60718293a4b5c6d7e8f90"
JWT = jwt(2000000000)
CEDULA = "0912345678"
CORREO = "alumno.secreto@unemi.edu.ec"
CURSO = "CÁLCULO DIFERENCIAL SECRETO"
NOMBRE = "PEREZ GOMEZ JUAN"


# ----------------------------- depurar() -----------------------------------
@pytest.mark.parametrize("texto,no_debe_aparecer", [
    (f"fallo https://x.ec/webservice/rest/server.php?wstoken={TOKEN_MOODLE}&wsfunction=a", TOKEN_MOODLE),
    (f"enlace moodlemobile://token=QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVo=", "QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVo"),
    (f"Authorization: Bearer {JWT}", JWT),
    (f"jwt suelto {JWT}", JWT),
    (f"correo {CORREO}", CORREO),
    (f"cedula {CEDULA}", CEDULA),
    ("login?username=u&password=MiClaveSecreta&service=s", "MiClaveSecreta"),
    (f"hex {TOKEN_MOODLE}", TOKEN_MOODLE),
])
def test_depurar_oculta_secretos(texto, no_debe_aparecer):
    assert no_debe_aparecer not in depurar(texto)


def test_depurar_no_destruye_mensajes_utiles():
    assert depurar("Token expirado: vuelve a iniciar sesión (HTTP 401)") == "Token expirado: vuelve a iniciar sesión (HTTP 401)"


def test_enmascarar_quita_el_nombre_del_alumno():
    r = enmascarar(f"{NOMBRE}, {CEDULA} - CÁLCULO I")
    assert NOMBRE not in r and CEDULA not in r and "CÁLCULO I" in r


def test_resumir_estructura_no_muestra_valores():
    lineas = " ".join(resumir_estructura({"a": "SECRETO", "b": [{"c": 7}], "d": None}))
    assert "SECRETO" not in lineas and "a: str" in lineas and "b: lista de 1" in lineas


def test_alias_es_estable():
    a = Alias("Curso")
    assert a("X") == "Curso 1" and a("Y") == "Curso 2" and a("X") == "Curso 1"


# ------------- errores de Moodle: la URL lleva el wstoken ------------------
def test_error_de_red_de_moodle_no_filtra_el_token():
    from moodle.client import MoodleClient

    class SesionRota:
        def get(self, url, params=None):
            raise requests.HTTPError(
                f"403 Client Error: Forbidden for url: {url}?wstoken={params['wstoken']}&wsfunction={params['wsfunction']}")
    c = MoodleClient("https://aula.ejemplo.ec", token=TOKEN_MOODLE)
    c.session = SesionRota()
    with pytest.raises(RuntimeError) as e:
        c.call("core_webservice_get_site_info")
    assert TOKEN_MOODLE not in str(e.value) and "core_webservice_get_site_info" in str(e.value)
    assert e.value.__suppress_context__, "la excepcion original (con la URL) no debe mostrarse en el traceback"


def test_login_de_moodle_no_filtra_la_contrasena():
    from moodle.client import MoodleClient

    class SesionRota:
        def get(self, url, params=None):
            raise requests.ConnectionError(f"fallo en {url}?username=u&password={params['password']}")
    c = MoodleClient("https://aula.ejemplo.ec")
    c.session = SesionRota()
    with pytest.raises(RuntimeError) as e:
        c.login("u", "MiClaveSecreta")
    assert "MiClaveSecreta" not in str(e.value)


def test_el_detalle_guardado_de_un_fallo_de_moodle_no_contiene_el_token(bd_vacia):
    from core.sync_all import sync
    from db.setup import get_connection

    class MoodleRoto:
        token = TOKEN_MOODLE

        def get_site_info(self):
            raise RuntimeError(depurar(f"Error de red: ...server.php?wstoken={TOKEN_MOODLE}&x=1"))
    # simula que el cliente real ya depuro; ademas sync_all depura de nuevo lo que guarda
    class MoodleSinDepurar(MoodleRoto):
        def get_site_info(self):
            raise RuntimeError(f"Error: ...server.php?wstoken={TOKEN_MOODLE}&x=1")
    try:
        sync(estudiante_id=77, moodle_client=MoodleSinDepurar(), sga_client=None, sga_alumno_id="")
    except Exception:
        pass
    c = get_connection()
    detalles = [r["detalle"] or "" for r in c.execute("select detalle from sesion_extraccion")]
    c.close()
    assert detalles and all(TOKEN_MOODLE not in d for d in detalles)


# ------------------------- volcados locales ---------------------------------
def test_guardar_local_va_a_una_carpeta_ignorada(tmp_path):
    ruta = guardar_local("horario_raw.json", {"dato": "privado"}, carpeta=str(tmp_path / "diagnostico_local"))
    assert pathlib.Path(ruta).parent.name == "diagnostico_local"
    assert (tmp_path / "diagnostico_local" / ".gitignore").read_text().strip() == "*"
    assert json.loads(pathlib.Path(ruta).read_text(encoding="utf-8")) == {"dato": "privado"}


def test_gitignore_cubre_volcados_y_secretos():
    reglas = {l.strip() for l in (RAIZ / ".gitignore").read_text().splitlines()}
    for r in ("diagnostico_local/", "*_raw.json", ".env", "*.db", ".session_key"):
        assert r in reglas, r


def test_ningun_volcado_crudo_esta_versionado():
    if not (RAIZ / ".git").exists():
        pytest.skip("sin repositorio git")
    archivos = subprocess.run(["git", "ls-files"], cwd=RAIZ, capture_output=True, text=True).stdout.splitlines()
    assert not [a for a in archivos if a.endswith("_raw.json") or a.startswith("diagnostico_local/")]


# ----------------- reglas estaticas sobre los scripts -----------------------
SCRIPTS = sorted(DIAG.glob("*.py"))


def test_hay_scripts_de_diagnostico():
    assert {s.name for s in SCRIPTS} >= {"debug_horario.py", "debug_malla.py", "debug_registro.py",
                                         "debug_calificaciones.py", "debug_notas_moodle.py",
                                         "debug_materias_display.py", "diagnostico_sga.py"}


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda s: s.name)
def test_scripts_no_usan_contrasena_ni_escriben_volcados_sueltos(script):
    texto = script.read_text(encoding="utf-8")
    assert "MOODLE_PASSWORD" not in texto and "MOODLE_USERNAME" not in texto and ".login(" not in texto
    assert "json.dump" not in texto, "los volcados se guardan con guardar_local()"


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda s: s.name)
def test_scripts_no_imprimen_variables_de_token(script):
    arbol = ast.parse(script.read_text(encoding="utf-8"))
    prohibidas = {"token", "refresh", "password", "access_token", "refresh_token"}
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.Call) and getattr(nodo.func, "id", "") in ("print", "imprimir"):
            for sub in ast.walk(nodo):
                if isinstance(sub, ast.Name) and sub.id in prohibidas:
                    pytest.fail(f"{script.name}:{nodo.lineno} imprime la variable {sub.id}")
                if isinstance(sub, ast.Attribute) and sub.attr in prohibidas:
                    pytest.fail(f"{script.name}:{nodo.lineno} imprime .{sub.attr}")


# ------------- ejecucion con servicios simulados: la salida es limpia -------
@pytest.fixture
def diag(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(DIAG))
    monkeypatch.setattr(privacidad, "CARPETA_LOCAL", str(tmp_path / "diagnostico_local"))

    def cargar(nombre):
        return importlib.import_module(nombre)
    return cargar


class SGAFalso:
    access_token = JWT
    refresh_token = "refresh-secreto"

    def cambiar_a_perfil_regular(self):
        return False

    def _datos(self):
        return {"data": {"x": f"{NOMBRE} {CEDULA} {CORREO}"}}

    def get_horario(self):
        return self._datos()

    def get_malla(self):
        return self._datos()

    def get_registro_academico(self, alumno_id):
        return self._datos()

    def get_materias(self):
        return {"data": {"eMateriasAsignadas": [{"display": f"{NOMBRE}, {CEDULA} - {CURSO} - {CORREO}"}]}}

    def get_alumno_id_automatico(self):
        return "123"

    def get_registro_parseado(self, alumno_id):
        return [{"periodo": "P1", "materia": CURSO, "nota": 80, "aprobada": True, "pendiente": False}]


class MoodleFalso:
    token = TOKEN_MOODLE

    def get_site_info(self):
        return {"userid": 77, "fullname": NOMBRE}

    def get_courses(self):
        return [{"id": 9, "fullname": CURSO}]

    def get_grades(self, cid, uid):
        return {"usergrades": [{"gradeitems": [
            {"id": 1, "itemtype": "mod", "itemmodule": "assign", "itemname": f"S1-TAREA_1 (N1) {CURSO}", "categoryid": 5,
             "gradeformatted": "8,00", "percentageformatted": "80,00 %", "weightformatted": "20,00 %"},
            {"id": 5, "itemtype": "category", "itemname": "", "gradeformatted": "8,00", "percentageformatted": "80,00 %"}]}]}

    def call(self, fn, **kw):
        return {"tables": [{"tabledata": [{"itemname": {"content": f"<b>{CURSO}</b>"},
                                           "grade": {"content": "8,00"}, "percentage": {"content": "80,00 %"},
                                           "feedback": {"content": f"Buen trabajo {NOMBRE}"}}]}]}


SECRETOS = (CURSO, NOMBRE, CEDULA, CORREO, TOKEN_MOODLE, JWT, "refresh-secreto")


def _sin_secretos(capsys, permitir=()):
    salida = capsys.readouterr().out
    for s in SECRETOS:
        if s in permitir:
            continue
        assert s not in salida, f"se filtro: {s[:12]}..."
    return salida


@pytest.mark.parametrize("nombre", ["debug_horario", "debug_malla"])
def test_scripts_de_estructura_sga(diag, capsys, nombre):
    assert diag(nombre).main(client=SGAFalso()) == 0
    salida = _sin_secretos(capsys)
    assert "data.x: str" in salida and "diagnostico_local" in salida


def test_debug_registro(diag, capsys):
    assert diag("debug_registro").main(client=SGAFalso(), alumno_id="123") == 0
    assert "data.x: str" in _sin_secretos(capsys)


def test_debug_materias_display(diag, capsys):
    assert diag("debug_materias_display").main(client=SGAFalso()) == 0
    # su objetivo es ver el formato del texto: el nombre de la materia SI se muestra,
    # pero no el nombre del alumno, la cedula, el correo ni tokens
    assert "[NOMBRE]" in _sin_secretos(capsys, permitir=(CURSO,))


def test_debug_calificaciones(diag, capsys):
    assert diag("debug_calificaciones").main(client=MoodleFalso()) == 0
    salida = _sin_secretos(capsys)
    assert "itemtype='category'" in salida and "itemname=vacio" in salida and "etiqueta_componente=N1" in salida


def test_debug_notas_moodle_oculta_nombres_pero_muestra_numeros(diag, capsys):
    assert diag("debug_notas_moodle").main(cliente=MoodleFalso()) == 0
    salida = _sin_secretos(capsys)
    assert "Curso 1" in salida and "Item 1" in salida
    assert "80,00 %" in salida and "20,00 %" in salida, "los numeros son lo que se necesita ver"
    assert "feedback=[oculto]" in salida


def test_diagnostico_sga_por_defecto_no_muestra_datos(diag, capsys):
    assert diag("diagnostico_sga").main(con_datos=False, cliente=SGAFalso()) == 0
    salida = _sin_secretos(capsys)
    assert "--con-datos" in salida


def test_diagnostico_sga_con_datos_sigue_enmascarando(diag, capsys):
    assert diag("diagnostico_sga").main(con_datos=True, cliente=SGAFalso()) == 0
    salida = capsys.readouterr().out
    for s in (NOMBRE, CEDULA, CORREO, JWT, "refresh-secreto"):
        assert s not in salida
