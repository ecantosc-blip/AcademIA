"""
Preferencias del estudiante para su plan de estudio y sus alertas (Fase 3).

Todo lo escribe la propia persona y se valida aqui; ningun valor se infiere ni se inventa:
mientras no indique su disponibilidad, el plan lo dice en vez de suponer cuantas horas tiene.
"""
import json
import re

DIAS = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]
TIPOS_ALERTA = ["vence_pronto", "vencida", "lectura_fallida", "asistencia_baja"]
HORAS_ALERTA = (12, 24, 48, 72)
MAX_MIN_DIA = 720

# Valores de partida (el estudiante los puede cambiar). La disponibilidad NO tiene valor de partida.
POR_DEFECTO = {
    "disponibilidad": {},
    "minutos_por_tarea": 60,
    "minutos_evaluacion": 120,
    "alerta_horas": 48,
    "alertas_tipos": list(TIPOS_ALERTA),
    "asistencia_minima": None,
}
_CLAVE_PLAN = re.compile(r"^(t:\d{1,12}|e:.{1,200})$")


def _entero(valor, nombre, minimo, maximo):
    if isinstance(valor, bool) or not isinstance(valor, (int, float, str)):
        raise ValueError(f"{nombre}: indica un número.")
    try:
        n = float(str(valor).replace(",", "."))
    except ValueError:
        raise ValueError(f"{nombre}: indica un número.") from None
    if n != int(n) or not minimo <= n <= maximo:
        raise ValueError(f"{nombre}: debe ser un número entero entre {minimo} y {maximo}.")
    return int(n)


def _validar(clave, valor):
    if clave == "disponibilidad":
        if not isinstance(valor, dict):
            raise ValueError("Disponibilidad: indica los minutos de cada día.")
        limpio = {}
        for dia, minutos in valor.items():
            if dia not in DIAS:
                raise ValueError(f"Disponibilidad: «{dia}» no es un día válido.")
            m = _entero(minutos, f"Disponibilidad del {dia}", 0, MAX_MIN_DIA)
            if m:
                limpio[dia] = m
        return limpio
    if clave == "minutos_por_tarea":
        return _entero(valor, "Minutos por tarea", 15, 480)
    if clave == "minutos_evaluacion":
        return _entero(valor, "Minutos por evaluación", 15, 600)
    if clave == "alerta_horas":
        n = _entero(valor, "Aviso de entregas", 1, 720)
        if n not in HORAS_ALERTA:
            raise ValueError(f"Aviso de entregas: elige entre {', '.join(map(str, HORAS_ALERTA))} horas.")
        return n
    if clave == "alertas_tipos":
        if not isinstance(valor, list) or any(t not in TIPOS_ALERTA for t in valor):
            raise ValueError("Alertas: tipo no válido.")
        return [t for t in TIPOS_ALERTA if t in valor]
    if clave == "asistencia_minima":
        if valor is None or valor == "":
            return None
        return float(_entero(valor, "Asistencia mínima", 0, 100))
    raise ValueError(f"Preferencia desconocida: {clave}.")


def obtener(conn, estudiante_id: int) -> dict:
    """Preferencias guardadas, completadas con los valores de partida."""
    resultado = json.loads(json.dumps(POR_DEFECTO))
    for f in conn.execute("SELECT clave, valor FROM preferencia WHERE estudiante_id = ?", (estudiante_id,)):
        if f["clave"] in resultado:
            try:
                resultado[f["clave"]] = json.loads(f["valor"])
            except (ValueError, TypeError):
                pass                                   # valor ilegible: se queda el de partida
    return resultado


def guardar(conn, estudiante_id: int, cambios: dict) -> dict:
    """Valida TODO antes de guardar (si algo falla, no se guarda nada). Devuelve las preferencias vigentes."""
    if not isinstance(cambios, dict) or not cambios:
        raise ValueError("No hay nada que guardar.")
    validados = {k: _validar(k, v) for k, v in cambios.items()}
    for clave, valor in validados.items():
        conn.execute("""
            INSERT INTO preferencia (estudiante_id, clave, valor) VALUES (?, ?, ?)
            ON CONFLICT(estudiante_id, clave) DO UPDATE SET valor = excluded.valor
        """, (estudiante_id, clave, json.dumps(valor)))
    conn.commit()
    return obtener(conn, estudiante_id)


# ---- dificultad por materia ------------------------------------------------
def dificultades(conn, estudiante_id: int) -> dict:
    return {f["materia_id"]: f["nivel"] for f in conn.execute(
        "SELECT materia_id, nivel FROM dificultad_materia WHERE estudiante_id = ?", (estudiante_id,))}


def guardar_dificultad(conn, estudiante_id: int, materia_id, nivel) -> None:
    """nivel 1 (fácil), 2 (media), 3 (difícil); None la quita. Solo para materias que la persona cursa."""
    materia_id = _entero(materia_id, "Materia", 1, 10**9)
    if not conn.execute("SELECT 1 FROM matricula WHERE estudiante_id = ? AND materia_id = ?",
                        (estudiante_id, materia_id)).fetchone():
        raise ValueError("Esa materia no está entre las tuyas.")
    if nivel is None or nivel == "":
        conn.execute("DELETE FROM dificultad_materia WHERE estudiante_id = ? AND materia_id = ?",
                     (estudiante_id, materia_id))
    else:
        nivel = _entero(nivel, "Dificultad", 1, 3)
        conn.execute("""
            INSERT INTO dificultad_materia (estudiante_id, materia_id, nivel) VALUES (?, ?, ?)
            ON CONFLICT(estudiante_id, materia_id) DO UPDATE SET nivel = excluded.nivel
        """, (estudiante_id, materia_id, nivel))
    conn.commit()


# ---- elementos quitados del plan ------------------------------------------------
def excluidos(conn, estudiante_id: int) -> set:
    return {f["clave"] for f in conn.execute("SELECT clave FROM plan_excluido WHERE estudiante_id = ?", (estudiante_id,))}


def excluir(conn, estudiante_id: int, clave: str) -> None:
    if not isinstance(clave, str) or not _CLAVE_PLAN.match(clave):
        raise ValueError("Elemento no válido.")
    conn.execute("INSERT OR IGNORE INTO plan_excluido (estudiante_id, clave) VALUES (?, ?)", (estudiante_id, clave))
    conn.commit()


def incluir(conn, estudiante_id: int, clave: str) -> None:
    conn.execute("DELETE FROM plan_excluido WHERE estudiante_id = ? AND clave = ?", (estudiante_id, clave))
    conn.commit()
