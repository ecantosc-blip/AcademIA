"""
Manejo unico de fechas academicas: siempre hora de Ecuador (America/Guayaquil, UTC-5,
sin horario de verano).

Por que existe: Moodle entrega las fechas como timestamp Unix (UTC). Antes se
convertian con la zona horaria del SERVIDOR (`datetime.fromtimestamp`): en tu
computadora coincidia con Ecuador, pero en un servidor en UTC (Render, Docker) una
entrega a las 23:59 se mostraba 5 horas despues.

Reglas (para evitar desfases y conversiones dobles):
  * UNA sola conversion de entrada: timestamp UTC -> hora de Ecuador (timestamp_a_iso).
  * En la base se guarda la hora local de Ecuador como texto ISO SIN zona
    ("2026-10-24T23:59:00"): es el mismo formato de antes, asi que no hace falta
    migrar. Como Ecuador no cambia de hora en el ano, no hay horas ambiguas.
  * "Ahora" siempre sale de ahora_iso(), nunca de datetime.now().
  * Un texto ISO sin zona se interpreta SIEMPRE como hora de Ecuador (iso_a_datetime).
"""
from datetime import datetime, timedelta, timezone

NOMBRE_ZONA = "America/Guayaquil"
_RESPALDO = timezone(timedelta(hours=-5), NOMBRE_ZONA)


def _cargar_zona(fabrica=None):
    """ZoneInfo si hay base de zonas horarias (en Windows hace falta el paquete
    `tzdata`); si no, el desfase fijo -05:00, que es exacto para Ecuador continental."""
    try:
        if fabrica is None:
            from zoneinfo import ZoneInfo as fabrica
        return fabrica(NOMBRE_ZONA)
    except Exception:
        return _RESPALDO


ZONA = _cargar_zona()


def _reloj() -> datetime:
    """Hora actual en UTC (las pruebas lo sustituyen para simular medianoche)."""
    return datetime.now(timezone.utc)


def ahora() -> datetime:
    """Hora actual de Ecuador, con zona."""
    return _reloj().astimezone(ZONA)


def ahora_iso(timespec: str = "auto", **delta) -> str:
    """Hora de Ecuador como texto ISO sin zona (formato guardado en la base).
    Admite un desplazamiento: ahora_iso('seconds', days=7), ahora_iso(days=-2)."""
    momento = ahora() + timedelta(**delta) if delta else ahora()
    return momento.replace(tzinfo=None).isoformat(timespec=timespec)


def timestamp_a_iso(ts) -> str:
    """Timestamp Unix (UTC) -> hora de Ecuador como ISO sin zona."""
    return datetime.fromtimestamp(float(ts), tz=timezone.utc).astimezone(ZONA).replace(tzinfo=None).isoformat()


def iso_a_datetime(iso: str) -> datetime:
    """ISO sin zona (hora de Ecuador) -> datetime con zona. Si trae zona, se convierte."""
    d = datetime.fromisoformat(iso)
    return d.replace(tzinfo=ZONA) if d.tzinfo is None else d.astimezone(ZONA)


def iso_a_timestamp(iso: str) -> float:
    """ISO sin zona (hora de Ecuador) -> timestamp Unix (UTC)."""
    return iso_a_datetime(iso).timestamp()
