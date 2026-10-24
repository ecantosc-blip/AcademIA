"""
Detector de cambios (Fase 1).

Problema que resuelve: Moodle y el ERP ya muestran tareas y notas, pero no avisan que
algo CAMBIO (una tarea nueva, una fecha movida, una nota publicada). Este modulo compara
lo que quedo guardado tras una sincronizacion exitosa con lo que quedo tras la anterior.

Reglas (todas probadas en tests/test_cambios.py):
  * Solo se compara entre sincronizaciones EXITOSAS. Quien llama registra la foto unicamente
    cuando la sincronizacion de esa plataforma termino bien; un fallo no toca la foto, asi que
    nunca se lee como "los datos cambiaron".
  * La primera vez solo se guarda la foto de partida: no hay con que comparar, y declarar
    "todo es nuevo" seria falso.
  * Sin duplicados: cada foto se compara una sola vez con la anterior; dos sincronizaciones
    seguidas sin cambios no generan nada.
  * Lo que desaparece NO se reporta: las listas de Moodle (eventos proximos) pierden elementos
    solo por el paso del tiempo. Tampoco es cambio que un estado de entrega pase de "sin
    verificar" a conocido: eso es verificar, no que la tarea haya cambiado.
  * Nada se interpreta ni se predice: se informa el valor de antes y el de despues.
"""
import json

from core.numeros import a_numero
from core.tiempo import ahora_iso

VERSION_FOTO = 1
_TOLERANCIA = 0.005
_MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]


# ---------------------------------------------------------------- fotos
def foto_moodle(conn, estudiante_id: int) -> dict:
    """Estado actual guardado de Moodle para este estudiante (tareas, eventos, notas)."""
    tareas = {}
    for f in conn.execute("""
        SELECT t.materia_id, t.moodle_assign_id, t.nombre, t.fecha_entrega, e.estado
        FROM tarea t
        JOIN matricula m ON m.materia_id = t.materia_id AND m.estudiante_id = ?
        LEFT JOIN entrega e ON e.tarea_id = t.id AND e.estudiante_id = ?
        GROUP BY t.id
    """, (estudiante_id, estudiante_id)):
        tareas[f"{f['materia_id']}:{f['moodle_assign_id']}"] = {
            "materia_id": f["materia_id"], "nombre": f["nombre"],
            "fecha": f["fecha_entrega"], "estado": f["estado"]}
    eventos = [{"nombre": f["nombre"], "fecha": f["fecha"]} for f in conn.execute(
        "SELECT nombre, fecha FROM evento WHERE estudiante_id = ? ORDER BY fecha, nombre", (estudiante_id,))]
    notas = {}
    for f in conn.execute("""
        SELECT m.materia_id, c.nombre_item, c.calificacion
        FROM componente_nota c JOIN matricula m ON m.id = c.matricula_id
        WHERE m.estudiante_id = ?
    """, (estudiante_id,)):
        notas[f"{f['materia_id']}:{f['nombre_item']}"] = {
            "materia_id": f["materia_id"], "item": f["nombre_item"], "valor": f["calificacion"]}
    return {"v": VERSION_FOTO, "tareas": tareas, "eventos": eventos, "notas": notas}


def foto_sga(conn, estudiante_id: int) -> dict:
    """Estado actual guardado del ERP: nota final y asistencia por materia."""
    materias = {}
    for f in conn.execute("""
        SELECT materia_id, nota_final, asistencia FROM matricula
        WHERE estudiante_id = ? AND (nota_final IS NOT NULL OR asistencia IS NOT NULL)
    """, (estudiante_id,)):
        materias[str(f["materia_id"])] = {"materia_id": f["materia_id"],
                                          "nota_final": f["nota_final"], "asistencia": f["asistencia"]}
    return {"v": VERSION_FOTO, "materias": materias}


_FOTOGRAFOS = {"moodle": foto_moodle, "sga": foto_sga}


# ---------------------------------------------------------------- comparacion
def _igual_numero(a, b) -> bool:
    na, nb = a_numero(a), a_numero(b)
    if na is not None and nb is not None:
        return abs(na - nb) <= _TOLERANCIA
    return (a or "") == (b or "")


def _cambio(tipo, materia_id, item, antes, despues):
    return {"tipo": tipo, "materia_id": materia_id, "item": item,
            "antes": None if antes is None else str(antes),
            "despues": None if despues is None else str(despues)}


def comparar_moodle(antes: dict, despues: dict) -> list:
    cambios = []
    for clave, t in despues.get("tareas", {}).items():
        previa = antes.get("tareas", {}).get(clave)
        if previa is None:
            cambios.append(_cambio("tarea_nueva", t["materia_id"], t["nombre"], None, t["fecha"]))
            continue
        if (previa["fecha"] or None) != (t["fecha"] or None):
            cambios.append(_cambio("tarea_fecha", t["materia_id"], t["nombre"], previa["fecha"], t["fecha"]))
        if previa["estado"] and t["estado"] and previa["estado"] != t["estado"]:
            cambios.append(_cambio("tarea_estado", t["materia_id"], t["nombre"], previa["estado"], t["estado"]))

    def _por_nombre(foto):
        agrupado = {}
        for e in foto.get("eventos", []):
            agrupado.setdefault(e["nombre"], []).append(e["fecha"])
        return agrupado

    ev_antes, ev_despues = _por_nombre(antes), _por_nombre(despues)
    for nombre, fechas in ev_despues.items():
        if nombre not in ev_antes:
            cambios.append(_cambio("evento_nuevo", None, nombre, None, fechas[0]))
        elif len(fechas) == 1 and len(ev_antes[nombre]) == 1 and fechas[0] != ev_antes[nombre][0]:
            cambios.append(_cambio("evento_fecha", None, nombre, ev_antes[nombre][0], fechas[0]))

    for clave, n in despues.get("notas", {}).items():
        previa = antes.get("notas", {}).get(clave)
        ahora_num = a_numero(n["valor"]) is not None
        if previa is None:
            if ahora_num:                      # un item nuevo sin nota ("-") todavia no es una novedad
                cambios.append(_cambio("nota_nueva", n["materia_id"], n["item"], None, n["valor"]))
        elif not _igual_numero(previa["valor"], n["valor"]):
            tipo = "nota_nueva" if (a_numero(previa["valor"]) is None and ahora_num) else "nota_cambio"
            cambios.append(_cambio(tipo, n["materia_id"], n["item"], previa["valor"], n["valor"]))
    return cambios


def comparar_sga(antes: dict, despues: dict) -> list:
    cambios = []
    for clave, m in despues.get("materias", {}).items():
        previa = antes.get("materias", {}).get(clave, {})
        for campo, tipo in (("nota_final", "sga_nota_final"), ("asistencia", "sga_asistencia")):
            nuevo, viejo = m.get(campo), previa.get(campo)
            if nuevo is None:
                continue                       # que el ERP deje de informar un dato no es un cambio de nota
            if viejo is None or abs(float(nuevo) - float(viejo)) > _TOLERANCIA:
                cambios.append(_cambio(tipo, m["materia_id"], None, viejo, nuevo))
    return cambios


_COMPARADORES = {"moodle": comparar_moodle, "sga": comparar_sga}


# ---------------------------------------------------------------- registro
def registrar(conn, estudiante_id: int, plataforma: str) -> int:
    """
    Llamar SOLO tras una sincronizacion exitosa de `plataforma`, con los datos ya guardados.
    Compara la foto actual con la anterior, guarda los cambios y reemplaza la foto.
    Devuelve cuantos cambios nuevos se registraron (0 en la primera vez).
    No hace commit: lo hace quien llama, en la misma transaccion.
    """
    ahora = ahora_iso("seconds")
    despues = _FOTOGRAFOS[plataforma](conn, estudiante_id)
    fila = conn.execute("SELECT datos FROM foto_estado WHERE estudiante_id = ? AND plataforma = ?",
                        (estudiante_id, plataforma)).fetchone()
    antes = None
    if fila:
        try:
            antes = json.loads(fila["datos"])
        except (ValueError, TypeError):
            antes = None
        if not isinstance(antes, dict) or antes.get("v") != VERSION_FOTO:
            antes = None                       # foto ilegible o de otro formato: se toma como punto de partida
    cambios = _COMPARADORES[plataforma](antes, despues) if antes is not None else []
    for c in cambios:
        conn.execute("""
            INSERT INTO cambio (estudiante_id, plataforma, tipo, materia_id, item, antes, despues, detectado_en)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (estudiante_id, plataforma, c["tipo"], c["materia_id"], c["item"], c["antes"], c["despues"], ahora))
    conn.execute("""
        INSERT INTO foto_estado (estudiante_id, plataforma, creada_en, datos) VALUES (?, ?, ?, ?)
        ON CONFLICT(estudiante_id, plataforma) DO UPDATE SET creada_en = excluded.creada_en, datos = excluded.datos
    """, (estudiante_id, plataforma, ahora, json.dumps(despues, ensure_ascii=False, sort_keys=True)))
    return len(cambios)


# ---------------------------------------------------------------- lectura
def _fecha_humana(iso):
    if not iso:
        return "sin fecha"
    try:
        f, h = iso[:10], iso[11:16]
        return f"{int(f[8:10])} {_MESES[int(f[5:7]) - 1]}, {h}" if h else f"{int(f[8:10])} {_MESES[int(f[5:7]) - 1]}"
    except (ValueError, IndexError):
        return str(iso)


def _num(valor):
    """Moodle ya entrega notas formateadas ("7,00"): se muestran tal cual. Los numeros del ERP
    (66.7) se escriben con coma y hasta 2 decimales, sin ceros de relleno."""
    n = a_numero(valor)
    if n is None or "," in str(valor):
        return str(valor)
    return f"{n:.2f}".rstrip("0").rstrip(".").replace(".", ",")


def describir(c: dict) -> str:
    t, item = c["tipo"], c.get("item") or ""
    if t == "tarea_nueva":
        return f"Nueva tarea: {item}" + (f" (vence {_fecha_humana(c['despues'])})" if c.get("despues") else " (sin fecha de entrega)")
    if t == "tarea_fecha":
        return f"Cambió la fecha de {item}: de {_fecha_humana(c['antes'])} a {_fecha_humana(c['despues'])}"
    if t == "tarea_estado":
        nombres = {"entregada": "entregada", "sin_entregar": "sin entregar", "borrador": "en borrador"}
        return f"{item}: ahora figura {nombres.get(c['despues'], c['despues'])} (antes {nombres.get(c['antes'], c['antes'])})"
    if t == "nota_nueva":
        return f"Nota publicada en {item}: {_num(c['despues'])}"
    if t == "nota_cambio":
        return f"Cambió la nota de {item}: de {_num(c['antes'])} a {_num(c['despues'])}"
    if t == "evento_nuevo":
        return f"Nuevo evento: {item} ({_fecha_humana(c['despues'])})"
    if t == "evento_fecha":
        return f"Cambió la fecha de {item}: de {_fecha_humana(c['antes'])} a {_fecha_humana(c['despues'])}"
    if t == "sga_nota_final":
        return "Nota final en el ERP: " + (f"de {_num(c['antes'])} a {_num(c['despues'])}" if c.get("antes") else f"{_num(c['despues'])} (nueva)")
    if t == "sga_asistencia":
        return "Asistencia en el ERP: " + (f"de {_num(c['antes'])}% a {_num(c['despues'])}%" if c.get("antes") else f"{_num(c['despues'])}% (nueva)")
    return item


_ETIQUETA = {"tarea_nueva": "Tarea", "tarea_fecha": "Fecha", "tarea_estado": "Entrega", "nota_nueva": "Nota",
             "nota_cambio": "Nota", "evento_nuevo": "Evento", "evento_fecha": "Fecha",
             "sga_nota_final": "Nota final", "sga_asistencia": "Asistencia"}


def novedades(conn, estudiante_id: int, solo_nuevas: bool = True, limite: int = 60) -> list:
    """Cambios del estudiante, los mas recientes primero. solo_nuevas = aun no marcados como vistos."""
    filtro = "AND c.visto_en IS NULL" if solo_nuevas else ""
    filas = conn.execute(f"""
        SELECT c.*, m.nombre AS materia FROM cambio c
        LEFT JOIN materia m ON m.id = c.materia_id
        WHERE c.estudiante_id = ? {filtro}
        ORDER BY c.detectado_en DESC, c.id DESC LIMIT ?
    """, (estudiante_id, limite)).fetchall()
    return [{"id": f["id"], "tipo": f["tipo"], "etiqueta": _ETIQUETA.get(f["tipo"], "Cambio"),
             "materia": f["materia"], "texto": describir(dict(f)), "detectado_en": f["detectado_en"],
             "visto": f["visto_en"] is not None} for f in filas]


def contar_nuevas(conn, estudiante_id: int) -> int:
    return conn.execute("SELECT COUNT(*) FROM cambio WHERE estudiante_id = ? AND visto_en IS NULL",
                        (estudiante_id,)).fetchone()[0]


def marcar_vistas(conn, estudiante_id: int) -> int:
    """Marca como vistas las novedades pendientes. Devuelve cuantas eran."""
    cur = conn.execute("UPDATE cambio SET visto_en = ? WHERE estudiante_id = ? AND visto_en IS NULL",
                       (ahora_iso("seconds"), estudiante_id))
    conn.commit()
    return cur.rowcount
