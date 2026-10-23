"""
Fechas academicas en America/Guayaquil: sin desfases ni conversiones dobles,
con casos cercanos a la medianoche y con el servidor en otras zonas horarias.
"""
import time
from datetime import datetime, timezone, timedelta

import pytest

from core import tiempo
from core.tiempo import (ahora, ahora_iso, timestamp_a_iso, iso_a_datetime, iso_a_timestamp, _cargar_zona)

UTC = timezone.utc


def ts_utc(*args):
    return datetime(*args, tzinfo=UTC).timestamp()


@pytest.fixture
def zona_del_servidor(monkeypatch):
    """Cambia la zona horaria del proceso (como si el servidor estuviera en otro pais)."""
    if not hasattr(time, "tzset"):
        pytest.skip("tzset no existe en Windows")
    original = time.tzname

    def poner(nombre):
        monkeypatch.setenv("TZ", nombre)
        time.tzset()
    yield poner
    monkeypatch.delenv("TZ", raising=False)
    time.tzset()


def reloj(monkeypatch, *args):
    monkeypatch.setattr(tiempo, "_reloj", lambda: datetime(*args, tzinfo=UTC))


# ---------------------- timestamp (UTC) -> hora de Ecuador -------------------
@pytest.mark.parametrize("utc,local", [
    ((2026, 10, 25, 4, 59, 0), "2026-10-24T23:59:00"),    # 23:59 en Guayaquil
    ((2026, 10, 25, 5, 0, 0), "2026-10-25T00:00:00"),     # medianoche exacta
    ((2026, 10, 25, 5, 1, 0), "2026-10-25T00:01:00"),     # un minuto despues
    ((2026, 10, 24, 4, 59, 59), "2026-10-23T23:59:59"),
    ((2026, 1, 1, 4, 0, 0), "2025-12-31T23:00:00"),       # cruce de ano
    ((2028, 3, 1, 4, 59, 0), "2028-02-29T23:59:00"),      # ano bisiesto
])
def test_conversion_cerca_de_medianoche(utc, local):
    assert timestamp_a_iso(ts_utc(*utc)) == local


@pytest.mark.parametrize("servidor", ["UTC", "Asia/Tokyo", "America/New_York", "America/Guayaquil", "Pacific/Auckland"])
def test_el_resultado_no_depende_de_la_zona_del_servidor(zona_del_servidor, servidor):
    zona_del_servidor(servidor)
    assert timestamp_a_iso(ts_utc(2026, 10, 25, 4, 59, 0)) == "2026-10-24T23:59:00"
    assert timestamp_a_iso(ts_utc(2026, 10, 25, 5, 0, 0)) == "2026-10-25T00:00:00"


def test_el_codigo_anterior_si_dependia_de_la_zona(zona_del_servidor):
    """Documenta el fallo que se corrigio: fromtimestamp usa la zona del servidor."""
    zona_del_servidor("UTC")
    antes = datetime.fromtimestamp(ts_utc(2026, 10, 25, 4, 59, 0)).isoformat()
    assert antes == "2026-10-25T04:59:00" != timestamp_a_iso(ts_utc(2026, 10, 25, 4, 59, 0))


def test_ida_y_vuelta_sin_conversion_doble():
    ts = ts_utc(2026, 10, 25, 4, 59, 0)
    iso = timestamp_a_iso(ts)
    assert iso_a_timestamp(iso) == ts
    assert timestamp_a_iso(iso_a_timestamp(iso)) == iso
    # convertir dos veces NO debe mover la hora
    assert timestamp_a_iso(iso_a_timestamp(timestamp_a_iso(ts))) == iso


@pytest.mark.parametrize("servidor", ["UTC", "Asia/Tokyo"])
def test_iso_sin_zona_se_lee_siempre_como_hora_de_ecuador(zona_del_servidor, servidor):
    zona_del_servidor(servidor)
    assert iso_a_timestamp("2026-10-24T23:59:00") == ts_utc(2026, 10, 25, 4, 59, 0)
    assert iso_a_datetime("2026-10-24T23:59:00").utcoffset() == timedelta(hours=-5)


def test_iso_con_zona_se_convierte_a_ecuador():
    assert iso_a_datetime("2026-10-25T04:59:00+00:00").replace(tzinfo=None) == datetime(2026, 10, 24, 23, 59)


# ------------------------------ "ahora" --------------------------------------
def test_ahora_cerca_de_medianoche(monkeypatch):
    reloj(monkeypatch, 2026, 10, 25, 4, 30, 0)
    assert ahora_iso("seconds") == "2026-10-24T23:30:00"
    reloj(monkeypatch, 2026, 10, 25, 5, 0, 0)
    assert ahora_iso("seconds") == "2026-10-25T00:00:00"


def test_ahora_con_desplazamiento(monkeypatch):
    reloj(monkeypatch, 2026, 10, 25, 4, 30, 0)
    assert ahora_iso("seconds", days=7) == "2026-10-31T23:30:00"
    assert ahora_iso("seconds", days=-2) == "2026-10-22T23:30:00"
    assert ahora_iso("seconds", hours=1) == "2026-10-25T00:30:00"


def test_ahora_iso_no_lleva_zona_y_tiene_el_formato_de_siempre(monkeypatch):
    reloj(monkeypatch, 2026, 10, 25, 4, 30, 5)
    assert "+" not in ahora_iso("seconds") and "Z" not in ahora_iso("seconds")
    assert datetime.fromisoformat(ahora_iso("seconds")) == datetime(2026, 10, 24, 23, 30, 5)
    assert ahora().tzinfo is not None


def test_respaldo_si_no_hay_base_de_zonas_horarias():
    def sin_tzdata(nombre):
        raise KeyError(nombre)
    z = _cargar_zona(sin_tzdata)
    assert datetime(2026, 10, 25, 4, 59, tzinfo=UTC).astimezone(z).replace(tzinfo=None) == datetime(2026, 10, 24, 23, 59)


def test_la_zona_real_es_ecuador():
    assert datetime(2026, 7, 1, 12, tzinfo=UTC).astimezone(tiempo.ZONA).utcoffset() == timedelta(hours=-5)
    assert datetime(2026, 12, 1, 12, tzinfo=UTC).astimezone(tiempo.ZONA).utcoffset() == timedelta(hours=-5)


# ---------------- integracion: sincronizacion y panel -----------------------
class MoodleFalso:
    token = "t"

    def __init__(self, ts):
        self.ts = ts

    def get_site_info(self):
        return {"fullname": "Prueba", "userid": 77}

    def get_courses(self):
        return [{"id": 1, "fullname": "MATERIA UNO - [A1] - A1 - SOFTWARE2019"}]

    def get_teachers(self, cid):
        return []

    def get_assignments(self, ids):
        return {"courses": [{"id": 1, "assignments": [{"id": 5, "name": "Entrega final", "duedate": self.ts}]}]}

    def get_upcoming_events(self):
        return {"events": [{"name": "Examen", "timestart": self.ts, "eventtype": "examen"}]}

    def get_all_grades(self):
        return []


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


@pytest.mark.parametrize("servidor", ["UTC", "Asia/Tokyo"])
def test_sincronizacion_guarda_hora_de_ecuador(bd_vacia, zona_del_servidor, servidor):
    zona_del_servidor(servidor)
    from core.sync_all import sync
    from db.setup import get_connection
    ts = ts_utc(2026, 10, 25, 4, 59, 0)      # 23:59 del 24 en Guayaquil
    sync(estudiante_id=77, moodle_client=MoodleFalso(ts), sga_client=SGAVacio(), sga_alumno_id="")
    c = get_connection()
    assert c.execute("select fecha_entrega from tarea").fetchone()[0] == "2026-10-24T23:59:00"
    assert c.execute("select fecha from evento").fetchone()[0] == "2026-10-24T23:59:00"
    c.close()


def _panel(monkeypatch, y, mo, d, h, mi):
    from panel import construir_panel
    reloj(monkeypatch, y, mo, d, h, mi, 0)
    return construir_panel({"estudiante_id": 77, "nombre": "X"})


def test_una_tarea_que_vence_a_las_2359_sigue_pendiente_a_las_2330_y_ya_no_a_las_0005(bd_vacia, monkeypatch):
    from db.setup import get_connection
    c = get_connection()
    c.execute("insert into estudiante(id,nombre) values (77,'X')")
    c.execute("insert into materia(id,nombre,moodle_course_id) values (1,'MATERIA UNO',1)")
    c.execute("insert into matricula(estudiante_id,materia_id,estado) values (77,1,'en_curso')")
    c.execute("insert into tarea(materia_id,moodle_assign_id,nombre,fecha_entrega) values (1,5,'Entrega final','2026-10-24T23:59:00')")
    c.commit()
    c.close()
    antes = _panel(monkeypatch, 2026, 10, 25, 4, 30)     # 23:30 del 24 en Ecuador
    assert [f["nombre"] for f in antes["fechas"]] == ["Entrega final"]
    assert antes["inicio"]["vencen_7d"] == 1
    despues = _panel(monkeypatch, 2026, 10, 25, 5, 5)    # 00:05 del 25 en Ecuador
    assert despues["fechas"] == [] and despues["inicio"]["vencen_7d"] == 0
