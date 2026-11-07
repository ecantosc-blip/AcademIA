"""
Horario personal del estudiante: cuando PUEDE estudiar y que hace en su dia a dia.

Todo lo dice la propia persona (normalmente conversando con el asistente) y aqui se valida;
nada se supone. La hora realmente libre de cada dia es:

    franjas libres que indico  -  clases de su horario (ERP)  -  actividades que indico

Las horas se guardan como "HH:MM". Una franja dura al menos 15 minutos.
"""
import re
import unicodedata

from core.preferencias import DIAS

MIN_FRANJA = 15
MAX_FILAS = 60
MAX_NOMBRE = 60
_HORA = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)(?::[0-5]\d)?$")


def normalizar_dia(valor) -> str:
    """'Miércoles ' -> 'miercoles'. Lanza ValueError si no es un dia."""
    t = unicodedata.normalize("NFD", str(valor or "")).encode("ascii", "ignore").decode().strip().lower()
    if t not in DIAS:
        raise ValueError(f"«{valor}» no es un día válido (lunes a domingo).")
    return t


def a_minutos(hora) -> int:
    """'18:30' -> 1110. Lanza ValueError si no es una hora clara."""
    m = _HORA.match(str(hora or "").strip())
    if not m:
        raise ValueError(f"«{hora}» no es una hora válida (usa el formato 18:30).")
    return int(m.group(1)) * 60 + int(m.group(2))


def a_texto(minutos: int) -> str:
    return f"{minutos // 60:02d}:{minutos % 60:02d}"


def _validar_rango(item, con_nombre=False):
    if not isinstance(item, dict):
        raise ValueError("Cada elemento debe indicar día, inicio y fin.")
    dia = normalizar_dia(item.get("dia"))
    ini, fin = a_minutos(item.get("inicio")), a_minutos(item.get("fin"))
    if fin - ini < MIN_FRANJA:
        raise ValueError(f"{dia} {a_texto(ini)}-{a_texto(fin)}: debe durar al menos {MIN_FRANJA} minutos y terminar después de empezar.")
    if not con_nombre:
        return dia, ini, fin, None
    nombre = re.sub(r"\s+", " ", str(item.get("nombre") or "")).strip()[:MAX_NOMBRE]
    if not nombre:
        raise ValueError(f"{dia} {a_texto(ini)}: indica qué actividad es.")
    return dia, ini, fin, nombre


def _unir(rangos):
    """Junta rangos (ini, fin) que se tocan o se solapan."""
    salida = []
    for ini, fin in sorted(rangos):
        if salida and ini <= salida[-1][1]:
            salida[-1] = (salida[-1][0], max(salida[-1][1], fin))
        else:
            salida.append((ini, fin))
    return salida


def _reemplazar(conn, est, tabla, items, con_nombre):
    if not isinstance(items, list):
        raise ValueError("Se esperaba una lista.")
    if len(items) > MAX_FILAS:
        raise ValueError(f"Demasiados elementos (máximo {MAX_FILAS}).")
    filas = [_validar_rango(i, con_nombre) for i in items]      # todo se valida antes de tocar la base
    if not con_nombre:                                          # las franjas libres que se tocan se unen
        por_dia = {}
        for dia, ini, fin, _ in filas:
            por_dia.setdefault(dia, []).append((ini, fin))
        filas = [(d, i, f, None) for d in DIAS for i, f in _unir(por_dia.get(d, []))]
    with conn:
        conn.execute(f"DELETE FROM {tabla} WHERE estudiante_id = ?", (est,))
        for dia, ini, fin, nombre in filas:
            if con_nombre:
                conn.execute("INSERT INTO actividad (estudiante_id, dia, inicio, fin, nombre) VALUES (?,?,?,?,?)",
                             (est, dia, a_texto(ini), a_texto(fin), nombre))
            else:
                conn.execute("INSERT INTO franja_libre (estudiante_id, dia, inicio, fin) VALUES (?,?,?,?)",
                             (est, dia, a_texto(ini), a_texto(fin)))
    return len(filas)


def guardar_franjas(conn, est, franjas) -> int:
    """Reemplaza TODAS las franjas libres de la persona (una lista vacia las borra)."""
    return _reemplazar(conn, est, "franja_libre", franjas, False)


def guardar_actividades(conn, est, actividades) -> int:
    """Reemplaza TODAS las actividades de la persona (una lista vacia las borra)."""
    return _reemplazar(conn, est, "actividad", actividades, True)


def listar(conn, est) -> dict:
    orden = "CASE dia " + " ".join(f"WHEN '{d}' THEN {i}" for i, d in enumerate(DIAS)) + " END, inicio"
    return {
        "franjas": [dict(r) for r in conn.execute(
            f"SELECT dia, inicio, fin FROM franja_libre WHERE estudiante_id = ? ORDER BY {orden}", (est,))],
        "actividades": [dict(r) for r in conn.execute(
            f"SELECT dia, inicio, fin, nombre FROM actividad WHERE estudiante_id = ? ORDER BY {orden}", (est,))],
    }


def _clases(conn, est) -> dict:
    """Clases del horario del ERP por dia: {dia: [(ini, fin)]}. Lo que no se entiende se ignora."""
    por_dia = {}
    for r in conn.execute("""
            SELECT DISTINCT h.dia, h.hora_inicio, h.hora_fin FROM horario h
            JOIN matricula mat ON mat.materia_id = h.materia_id AND mat.estudiante_id = ?""", (est,)):
        try:
            dia, ini, fin = normalizar_dia(r["dia"]), a_minutos(r["hora_inicio"]), a_minutos(r["hora_fin"])
        except ValueError:
            continue
        if fin > ini:
            por_dia.setdefault(dia, []).append((ini, fin))
    return por_dia


def _restar(libres, ocupados):
    resultado = []
    for ini, fin in libres:
        trozos = [(ini, fin)]
        for oi, of in ocupados:
            nuevos = []
            for ti, tf in trozos:
                if of <= ti or oi >= tf:
                    nuevos.append((ti, tf))
                    continue
                if oi > ti:
                    nuevos.append((ti, oi))
                if of < tf:
                    nuevos.append((of, tf))
            trozos = nuevos
        resultado += [t for t in trozos if t[1] - t[0] >= MIN_FRANJA]
    return resultado


def hay_franjas(conn, est) -> bool:
    return conn.execute("SELECT 1 FROM franja_libre WHERE estudiante_id = ? LIMIT 1", (est,)).fetchone() is not None


def ventanas_efectivas(conn, est) -> dict:
    """{dia: [(ini_min, fin_min)]} con la hora realmente libre de cada dia (puede quedar vacio)."""
    clases = _clases(conn, est)
    por_dia = {d: [] for d in DIAS}
    ocupado = {d: [] for d in DIAS}
    for r in conn.execute("SELECT dia, inicio, fin FROM franja_libre WHERE estudiante_id = ?", (est,)):
        por_dia[r["dia"]].append((a_minutos(r["inicio"]), a_minutos(r["fin"])))
    for r in conn.execute("SELECT dia, inicio, fin FROM actividad WHERE estudiante_id = ?", (est,)):
        ocupado[r["dia"]].append((a_minutos(r["inicio"]), a_minutos(r["fin"])))
    return {d: _restar(_unir(por_dia[d]), _unir(clases.get(d, []) + ocupado[d])) for d in DIAS}
