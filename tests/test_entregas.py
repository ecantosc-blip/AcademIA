"""Estado de entrega: interpretacion de Moodle, conservacion de estados y fallos."""
import pytest

from core import tiempo
from core.entregas import interpretar_estado, sincronizar_entregas
from db.setup import get_connection

TOKEN = "a1b2c3d4e5f60718293a4b5c6d7e8f90"


def resp(status, clave="submission"):
    return {"lastattempt": {clave: {"status": status}}}


# ------------------------ interpretacion de la respuesta ---------------------
@pytest.mark.parametrize("respuesta,esperado", [
    (resp("submitted"), "entregada"), (resp("draft"), "borrador"), (resp("new"), "sin_entregar"),
    (resp("SUBMITTED"), "entregada"), (resp(" submitted "), "entregada"),
    (resp("submitted", "teamsubmission"), "entregada"),
])
def test_estados_reconocidos(respuesta, esperado):
    assert interpretar_estado(respuesta) == esperado


@pytest.mark.parametrize("respuesta", [
    resp("reopened"), resp("algo_raro"), resp(""), resp(None),
    {}, None, [], "texto", {"lastattempt": None}, {"lastattempt": {}}, {"lastattempt": {"submission": None}},
    {"lastattempt": {"submission": {}}}, {"lastattempt": "x"}, {"warnings": [{"message": "no permitido"}]},
])
def test_lo_dudoso_queda_sin_verificar_y_no_se_inventa(respuesta):
    assert interpretar_estado(respuesta) is None


# ----------------------------- sincronizacion --------------------------------
class Moodle:
    def __init__(self, respuestas=None, error=None):
        self.respuestas = respuestas or {}
        self.error = error
        self.llamadas = []

    def get_submission_status(self, assign_id, userid):
        self.llamadas.append((assign_id, userid))
        r = self.respuestas.get(assign_id)
        if isinstance(r, Exception):
            raise r
        if self.error and r is None:
            raise self.error
        return r


@pytest.fixture
def bd(bd_vacia, monkeypatch):
    monkeypatch.setattr(tiempo, "_reloj", lambda: __import__("datetime").datetime(2026, 10, 25, 15, 0, 0,
                        tzinfo=__import__("datetime").timezone.utc))      # 10:00 del 25 en Ecuador
    c = get_connection()
    c.execute("insert into estudiante(id,nombre) values (77,'X')")
    c.execute("insert into materia(id,nombre,moodle_course_id) values (1,'M1',1)")
    c.commit()
    yield c
    c.close()


def tarea(c, tid, fecha, **extra):
    c.execute("insert into tarea(id,materia_id,moodle_assign_id,nombre,fecha_entrega) values (?,1,?,?,?)",
              (tid, 100 + tid, f"T{tid}", fecha))
    return {"tarea_id": tid, "assign_id": 100 + tid, "fecha_entrega": fecha, "sin_entrega_online": False, **extra}


def estados(c):
    return {r["tarea_id"]: r["estado"] for r in c.execute("select * from entrega")}


def test_guarda_estados_reconocidos_y_deja_sin_verificar_el_resto(bd):
    ts = [tarea(bd, 1, "2026-10-30T10:00:00"), tarea(bd, 2, "2026-10-31T10:00:00"), tarea(bd, 3, "2026-11-01T10:00:00")]
    m = Moodle({101: resp("submitted"), 102: resp("reopened"), 103: {}})
    r = sincronizar_entregas(bd.cursor(), m, 55, 77, ts)
    assert estados(bd) == {1: "entregada"}, "lo no reconocido NO se guarda"
    assert r["verificadas"] == 1 and r["sin_estado"] == 2 and r["errores"] == 0
    assert all(u == 55 for _, u in m.llamadas)
    row = bd.execute("select verificada_en from entrega").fetchone()[0]
    assert row == "2026-10-25T10:00:00"


def test_un_fallo_conserva_los_estados_ya_verificados(bd):
    ts = [tarea(bd, 1, "2026-10-30T10:00:00"), tarea(bd, 2, "2026-10-31T10:00:00")]
    sincronizar_entregas(bd.cursor(), Moodle({101: resp("draft"), 102: resp("new")}), 55, 77, ts)
    assert estados(bd) == {1: "borrador", 2: "sin_entregar"}
    # segunda sincronizacion: Moodle falla en todo
    r = sincronizar_entregas(bd.cursor(), Moodle(error=RuntimeError("Moodle caido")), 55, 77, ts)
    assert estados(bd) == {1: "borrador", 2: "sin_entregar"}, "no se pisan los estados anteriores"
    assert r["errores"] == 2 and r["verificadas"] == 0
    # tercera: falla solo una; la otra se actualiza
    r = sincronizar_entregas(bd.cursor(), Moodle({101: RuntimeError("x"), 102: resp("submitted")}), 55, 77, ts)
    assert estados(bd) == {1: "borrador", 2: "entregada"}


def test_una_respuesta_dudosa_posterior_tampoco_borra_un_estado_verificado(bd):
    ts = [tarea(bd, 1, "2026-10-30T10:00:00")]
    sincronizar_entregas(bd.cursor(), Moodle({101: resp("draft")}), 55, 77, ts)
    sincronizar_entregas(bd.cursor(), Moodle({101: {"warnings": []}}), 55, 77, ts)
    assert estados(bd) == {1: "borrador"}


def test_no_vuelve_a_preguntar_por_lo_ya_entregado(bd):
    ts = [tarea(bd, 1, "2026-10-30T10:00:00"), tarea(bd, 2, "2026-10-31T10:00:00")]
    sincronizar_entregas(bd.cursor(), Moodle({101: resp("submitted"), 102: resp("draft")}), 55, 77, ts)
    m = Moodle({102: resp("submitted")})
    sincronizar_entregas(bd.cursor(), m, 55, 77, ts)
    assert [a for a, _ in m.llamadas] == [102]
    assert estados(bd) == {1: "entregada", 2: "entregada"}


def test_si_las_primeras_consultas_fallan_se_detiene_y_lo_registra_sin_secretos(bd):
    ts = [tarea(bd, i, f"2026-10-{20+i}T10:00:00") for i in range(1, 8)]
    error = RuntimeError(f"Error de red: https://aula.ec/server.php?wstoken={TOKEN}&wsfunction=mod_assign_get_submission_status")
    m = Moodle(error=error)
    r = sincronizar_entregas(bd.cursor(), m, 55, 77, ts)
    assert r["abortada"] and len(m.llamadas) == 3 and r["errores"] == 3
    assert TOKEN not in r["detalle"] and "no disponible" in r["detalle"]
    assert estados(bd) == {}


def test_un_fallo_aislado_no_aborta(bd):
    ts = [tarea(bd, i, f"2026-10-{25+i}T10:00:00") for i in range(1, 6)]
    m = Moodle({101: resp("new"), 102: RuntimeError("uno"), 103: resp("new"), 104: resp("new"), 105: resp("new")})
    r = sincronizar_entregas(bd.cursor(), m, 55, 77, ts)
    assert not r["abortada"] and r["verificadas"] == 4 and r["errores"] == 1 and len(m.llamadas) == 5


def test_respeta_el_limite_y_prioriza_lo_vigente(bd):
    ts = [tarea(bd, 1, "2026-10-20T10:00:00"),      # vencida hace 5 dias
          tarea(bd, 2, "2026-10-28T10:00:00"),
          tarea(bd, 3, "2026-10-26T10:00:00"),
          tarea(bd, 4, None)]
    m = Moodle({i: resp("new") for i in range(101, 105)})
    r = sincronizar_entregas(bd.cursor(), m, 55, 77, ts, max_consultas=3)
    assert [a for a, _ in m.llamadas] == [103, 102, 104], "vigentes por cercania; sin fecha despues; vencidas al final"
    assert r["omitidas_por_limite"] == 1 and "limite" in r["detalle"]


def test_omite_tareas_antiguas_y_sin_entrega_en_linea(bd):
    ts = [tarea(bd, 1, "2026-08-01T10:00:00"),                              # > 30 dias
          tarea(bd, 2, "2026-10-30T10:00:00", sin_entrega_online=True),     # Moodle no puede saberlo
          tarea(bd, 3, "2026-10-30T10:00:00")]
    m = Moodle({103: resp("new")})
    sincronizar_entregas(bd.cursor(), m, 55, 77, ts)
    assert [a for a, _ in m.llamadas] == [103]


def test_variable_de_entorno_limita_las_consultas(bd, monkeypatch):
    monkeypatch.setenv("MAX_CONSULTAS_ENTREGA", "2")
    ts = [tarea(bd, i, f"2026-10-{25+i}T10:00:00") for i in range(1, 5)]
    m = Moodle({i: resp("new") for i in range(101, 105)})
    sincronizar_entregas(bd.cursor(), m, 55, 77, ts)
    assert len(m.llamadas) == 2
    monkeypatch.setenv("MAX_CONSULTAS_ENTREGA", "no-es-numero")
    m2 = Moodle({i: resp("new") for i in range(101, 105)})
    sincronizar_entregas(bd.cursor(), m2, 55, 77, ts)       # valor invalido -> usa el predeterminado
    assert len(m2.llamadas) == 4


# --------------------- integracion con la sincronizacion completa -------------
class MoodleCompleto:
    token = "t"

    def __init__(self, estado):
        self.estado = estado

    def get_site_info(self):
        return {"fullname": "Prueba", "userid": 55}

    def get_courses(self):
        return [{"id": 1, "fullname": "MATERIA UNO - [A1] - A1 - SOFTWARE2019"}]

    def get_teachers(self, cid):
        return []

    def get_assignments(self, ids):
        t = int(__import__("datetime").datetime(2026, 11, 1, 15, 0, tzinfo=__import__("datetime").timezone.utc).timestamp())
        return {"courses": [{"id": 1, "assignments": [{"id": 5, "name": "A", "duedate": t},
                                                      {"id": 6, "name": "B", "duedate": t},
                                                      {"id": 7, "name": "C", "duedate": t, "nosubmissions": 1}]}]}

    def get_upcoming_events(self):
        return {"events": []}

    def get_all_grades(self):
        return []

    def get_submission_status(self, assign_id, userid):
        r = self.estado(assign_id)
        if isinstance(r, Exception):
            raise r
        return r


class SGAVacio:
    access_token = "x"

    def cambiar_a_perfil_regular(self):
        return False

    def get_materias_parseadas(self):
        return []

    def get_horario_parseado(self):
        return []

    def get_registro_parseado(self, a):
        return []

    def get_malla_parseada(self):
        return []


def test_sync_completa_registra_el_resumen_y_no_falla_si_moodle_no_responde(bd_vacia):
    from core.sync_all import sync
    sync(estudiante_id=77, moodle_client=MoodleCompleto(lambda a: resp("submitted") if a == 5 else resp("reopened")),
         sga_client=SGAVacio(), sga_alumno_id="")
    c = get_connection()
    assert estados(c) == {1: "entregada"}
    det = c.execute("select detalle from sesion_extraccion where plataforma='moodle' and estado='exitosa'").fetchone()[0]
    assert "entregas verificadas: 1" in det and "sin estado reconocible: 1" in det
    # segunda vez: la funcion de Moodle revienta; la sincronizacion sigue exitosa y el estado se conserva
    sync(estudiante_id=77, moodle_client=MoodleCompleto(lambda a: RuntimeError(f"fallo wstoken={TOKEN}")),
         sga_client=SGAVacio(), sga_alumno_id="")
    assert estados(c) == {1: "entregada"}
    filas = c.execute("select estado, detalle from sesion_extraccion where plataforma='moodle' order by id").fetchall()
    assert [f["estado"] for f in filas] == ["exitosa", "exitosa"]
    assert TOKEN not in filas[-1]["detalle"] and "errores de consulta" in filas[-1]["detalle"]
    # "C" (sin entrega en linea) nunca se consulto ni se guardo
    c.close()
