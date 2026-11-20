"""
Plan de estudio explicable (Fase 3).

Problema que resuelve: la persona tiene varias entregas y evaluaciones a la vez y poco tiempo;
Moodle y el ERP muestran fechas sueltas, pero no ayudan a repartir el esfuerzo.

Como funciona (todo visible para el estudiante, nada de modelos ocultos):
  1. Entra al plan lo que esta CONFIRMADO como pendiente (tareas sin entregar y vencidas sin
     entregar) y las evaluaciones del calendario. Lo "sin verificar" se lista aparte: no se planifica.
  2. Cada elemento necesita minutos de estudio = minutos base (que la persona ajusta) x un factor
     por la DIFICULTAD que ella indico para esa materia (facil 0.75, media 1, dificil 1.5).
     Son estimaciones, no mediciones, y el plan lo dice.
  3. El tiempo disponible por dia lo escribe la persona. Si no lo ha indicado, no se supone nada.
  4. Se reparte lo mas urgente primero, dia a dia, sin pasar de la fecha limite. Si algo no cabe antes
     de vencer, se dice cuantos minutos faltan en vez de esconderlo.
  5. Cada bloque trae su "motivo". Lo que la persona quita del plan se puede volver a incluir.

Plan con horas (Fase 4): si la persona dijo en que franjas puede estudiar (con el asistente), el plan
usa esas horas menos sus clases y actividades, y cada bloque trae hora de inicio y fin. Ademas:
  * Las dos materias con menor porcentaje en MOODLE se adelantan hasta 2 y 1 dias en el orden (un
    empujon suave: nunca pasa por encima de algo ya vencido ni cambia las fechas limite).
  * Si sobra tiempo, se agrega un bloque de refuerzo diario (45 min como maximo) en esas materias.
  * Cada bloque trae una recomendacion de estudio fija (no la inventa un modelo).
"""
import re
from datetime import date as _date, timedelta

from core import agenda_personal as AP
from core import notas_moodle
from core import preferencias as P
from core.cambios import _fecha_humana
from core.tareas import clasificar_tareas
from core.tiempo import ahora_iso, iso_a_datetime

DIAS_PLAN = 7
BLOQUE_MIN = 15
MIN_TROZO = 30        # (solo en el plan con horas) al partir un elemento entre ventanas no se dejan trozos de menos de 30 min (salvo el ultimo)
FACTOR = {1: 0.75, 2: 1.0, 3: 1.5}
NOMBRE_DIFICULTAD = {1: "fácil", 2: "media", 3: "difícil"}
VENTANA_SIN_VERIFICAR = 30
# Eventos del calendario que parecen evaluaciones (se puede quitar cualquiera del plan).
_PARECE_EVALUACION = re.compile(r"examen|prueba|evaluaci|quiz|\btest\b|parcial|lecci[oó]n", re.I)


def _redondear(minutos: float) -> int:
    return max(BLOQUE_MIN, int(round(minutos / BLOQUE_MIN)) * BLOQUE_MIN)


def _fecha(iso):
    return iso_a_datetime(iso).date()


def _elementos(conn, est, ahora, prefs, dif, grupos):
    """(elementos, sin_verificar). Cada elemento: clave, nombre, materia, materia_id, fecha, tipo, vencida, minutos, base."""
    def minutos(base, materia_id):
        nivel = dif.get(materia_id, 2)
        return _redondear(base * FACTOR[nivel]), nivel

    elementos = []
    for grupo, vencida in (("vencidas_sin_entregar", True), ("pendientes", False)):
        for t in grupos[grupo]:
            m, nivel = minutos(prefs["minutos_por_tarea"], t["materia_id"])
            elementos.append({"clave": f"t:{t['tarea_id']}", "nombre": t["tarea"], "materia": t["materia"],
                              "materia_id": t["materia_id"], "fecha": t["fecha"], "tipo": "tarea",
                              "vencida": vencida, "minutos": m, "base": prefs["minutos_por_tarea"],
                              "nivel": nivel, "indicada": t["materia_id"] in dif})
    for e in conn.execute("SELECT nombre, fecha, materia_id FROM evento WHERE estudiante_id = ? AND fecha >= ? ORDER BY fecha",
                          (est, ahora)):
        if not _PARECE_EVALUACION.search(e["nombre"] or ""):
            continue
        m, nivel = minutos(prefs["minutos_evaluacion"], e["materia_id"])
        elementos.append({"clave": f"e:{e['nombre']}|{e['fecha']}", "nombre": e["nombre"], "materia": None,
                          "materia_id": e["materia_id"], "fecha": e["fecha"], "tipo": "evaluacion",
                          "vencida": False, "minutos": m, "base": prefs["minutos_evaluacion"],
                          "nivel": nivel, "indicada": e["materia_id"] in dif})
    corte = (iso_a_datetime(ahora) - timedelta(days=VENTANA_SIN_VERIFICAR)).replace(tzinfo=None).isoformat(timespec="seconds")
    sin_verificar = [t for t in grupos["sin_verificar"] if t["fecha"] is None or t["fecha"] >= corte]
    return elementos, sin_verificar


REFUERZO_MIN = 45
REFUERZO_MAX_MATERIAS = 2
TOPE_DIARIO_PORCENTAJE = 0.7   # no llena un dia de tiempo libre solo porque "cabe": deja margen,
                                # salvo que una fecha limite obligue a usarlo todo (ver _asignar_item)
RECOMENDACION = {
    "tarea": "Lee la consigna completa, divide el trabajo en partes y empieza por la más difícil. Haz una pausa de 5 minutos cada 25-50.",
    "evaluacion": "Repasa tus apuntes y resuelve ejercicios sin mirar la respuesta; al final explica el tema en voz alta como si se lo enseñaras a alguien.",
    "refuerzo": "Revisa el tema que menos te quedó claro en clase, haz 3 a 5 ejercicios y anota tus dudas para preguntarlas.",
}


def _aplicar_clip(ventanas, ahora_dt):
    """Hoy no se puede estudiar en el pasado: se recorta lo que ya paso (redondeado a 5 min)."""
    hoy_min = ahora_dt.hour * 60 + ahora_dt.minute
    hoy_min += (-hoy_min) % 5
    return [[max(i, hoy_min), f] for i, f in ventanas if f - max(i, hoy_min) >= BLOQUE_MIN]


def _asignar(ventanas, falta, expone_horas):
    """Toma hasta `falta` minutos de las ventanas de un dia, en orden. Devuelve [(inicio|None, fin|None, minutos)]."""
    trozos = []
    for v in ventanas:
        if falta <= 0:
            break
        libre = v[1] - v[0]
        toma = min(falta, libre)
        if toma < (MIN_TROZO if expone_horas else BLOQUE_MIN) and toma < falta:
            continue
        if toma <= 0:
            continue
        ini = v[0]
        v[0] += toma
        falta -= toma
        trozos.append((AP.a_texto(ini) if expone_horas else None, AP.a_texto(ini + toma) if expone_horas else None, toma))
    ventanas[:] = [v for v in ventanas if v[1] - v[0] >= BLOQUE_MIN]
    return trozos, falta


def _motivo(e):
    dificultad = (f"dificultad {NOMBRE_DIFICULTAD[e['nivel']]}" if e["indicada"]
                  else "dificultad media (no la indicaste)")
    estimado = f"Estimado: {e['minutos']} min ({e['base']} min base, {dificultad})."
    if e.get("empujon"):
        estimado += " Se adelantó porque en Moodle es una de tus materias con menor porcentaje."
    if e["vencida"]:
        return f"Ya venció y Moodle la indica sin entregar; conviene resolverla primero. {estimado}"
    if e["tipo"] == "evaluacion":
        return f"Es el {_fecha_humana(e['fecha'])}. {estimado}"
    if e["fecha"]:
        return f"Vence el {_fecha_humana(e['fecha'])}. {estimado}"
    return f"No tiene fecha de entrega. {estimado}"


def generar(conn, estudiante_id: int, ahora: str = None, dias: int = DIAS_PLAN) -> dict:
    """Calcula el plan de estudio de los proximos `dias` para un estudiante. El CODIGO decide horas y minutos (la IA solo conversa y propone): ordena pendientes por fecha con prioridad por nota baja en Moodle, descuenta clases y actividades, respeta los bloques fijados por la persona y devuelve el plan con el motivo de cada bloque."""
    ahora = ahora or ahora_iso("seconds")
    prefs, dif, excluidos = P.obtener(conn, estudiante_id), P.dificultades(conn, estudiante_id), P.excluidos(conn, estudiante_id)
    grupos = clasificar_tareas(conn, estudiante_id, ahora=ahora)
    elementos, sin_verificar = _elementos(conn, estudiante_id, ahora, prefs, dif, grupos)

    hoy = _fecha(ahora)
    ahora_dt = iso_a_datetime(ahora)
    calendario = [hoy + timedelta(days=i) for i in range(dias)]
    con_horas = AP.hay_franjas(conn, estudiante_id)
    if con_horas:
        efectivas = AP.ventanas_efectivas(conn, estudiante_id)
        ventanas = {d: [list(v) for v in efectivas[P.DIAS[d.weekday()]]] for d in calendario}
        ventanas[hoy] = _aplicar_clip(ventanas[hoy], ahora_dt)
    else:
        ventanas = {d: ([[0, prefs["disponibilidad"].get(P.DIAS[d.weekday()], 0)]]
                        if prefs["disponibilidad"].get(P.DIAS[d.weekday()], 0) else []) for d in calendario}
    capacidad = {d: sum(f - i for i, f in ventanas[d]) for d in calendario}
    configurado = any(capacidad.values())

    puestos = notas_moodle.prioridad(conn, estudiante_id)
    promedios = notas_moodle.promedios(conn, estudiante_id)
    for e in elementos:
        e["empujon"] = 2 if puestos.get(e["materia_id"]) == 1 else (1 if puestos.get(e["materia_id"]) == 2 else 0)

    quitados = [e for e in elementos if e["clave"] in excluidos]
    activos = [e for e in elementos if e["clave"] not in excluidos]

    def limite(e):
        """Ultimo dia en que se puede estudiar esto (None = sin limite dentro del plan)."""
        if e["vencida"] or not e["fecha"]:
            return None
        f = _fecha(e["fecha"])
        return f - timedelta(days=1) if e["tipo"] == "evaluacion" and f > hoy else f

    en_plan, fuera = [], []
    for e in activos:
        lim = limite(e)
        (fuera if lim is not None and lim > calendario[-1] else en_plan).append(e)

    def orden(e):
        grupo = 0 if e["vencida"] else (2 if not e["fecha"] else 1)
        urgencia = ((iso_a_datetime(e["fecha"]) - ahora_dt).total_seconds() / 86400 - e["empujon"]) if grupo == 1 else 0
        return (grupo, urgencia, e["fecha"] or "", e["nombre"])
    en_plan.sort(key=orden)

    bloques = {d: [] for d in calendario}
    no_alcanza, sin_espacio = [], []

    # Bloques que la persona movio o fijo a mano: se colocan primero, exactamente donde los puso.
    fijados, refuerzo_fijo = {}, set()
    if con_horas:
        por_clave = {e["clave"]: e for e in en_plan}
        nombres_m = {r["id"]: r["nombre"] for r in conn.execute("SELECT id, nombre FROM materia")}
        for pin in conn.execute("SELECT clave, fecha, inicio, fin FROM bloque_fijo WHERE estudiante_id = ? ORDER BY fecha, inicio", (estudiante_id,)):
            try:
                d = _date.fromisoformat(pin["fecha"])
                ini, fin = AP.a_minutos(pin["inicio"]), AP.a_minutos(pin["fin"])
            except ValueError:
                continue
            if d not in bloques:
                continue                                  # fuera de la semana del plan: se ignora
            e = por_clave.get(pin["clave"])
            if pin["clave"].startswith("r:"):
                try:
                    mid = int(pin["clave"][2:])
                except ValueError:
                    continue
                item, materia, tipo, motivo = f"Refuerzo: {nombres_m.get(mid, 'materia')}", None, "refuerzo", "Lo fijaste tú en este horario."
                refuerzo_fijo.add(d)
            elif e is not None:
                item, materia, tipo, motivo = e["nombre"], e["materia"], e["tipo"], "Lo fijaste tú en este horario. " + _motivo(e)
                fijados[e["clave"]] = fijados.get(e["clave"], 0) + (fin - ini)
            else:
                continue                                  # el elemento ya no esta en el plan (p. ej. ya lo entregaste)
            ventanas[d] = [list(v) for v in AP._restar([tuple(v) for v in ventanas[d]], [(ini, fin)])]
            bloques[d].append({"clave": pin["clave"], "item": item, "materia": materia, "minutos": fin - ini, "tipo": tipo,
                               "motivo": motivo, "inicio": pin["inicio"], "fin": pin["fin"], "fijado": True,
                               "recomendacion": RECOMENDACION[tipo]})

    if configurado:
        # Tope diario: por dia, lo nuevo que se planifica no pasa del TOPE_DIARIO_PORCENTAJE del tiempo
        # libre de ESE dia (capacidad[d], calculado antes de restar nada). Si para una tarea puntual
        # ese margen no alcanza antes de su fecha limite, para ESA tarea se ignora el tope (nunca se
        # sacrifica una fecha limite por dejar margen). Sin horas reales (con_horas=False) no aplica.
        cap_restante = {d: capacidad[d] * TOPE_DIARIO_PORCENTAJE for d in calendario} if con_horas else {}

        def _libre_real(d):
            return sum(f - i for i, f in ventanas[d])

        for e in en_plan:
            falta, lim = max(0, e["minutos"] - fijados.get(e["clave"], 0)), limite(e)
            if falta == 0:
                continue
            dias_elegibles = [d for d in calendario if lim is None or d <= lim]
            usar_tope = con_horas and sum(min(cap_restante[d], _libre_real(d)) for d in dias_elegibles) >= falta
            for d in calendario:
                if falta <= 0:
                    break
                if lim is not None and d > lim:
                    break
                tope = max(0.0, cap_restante[d]) if usar_tope else None
                tomar = falta if tope is None else min(falta, tope)
                if tope is not None and tomar < MIN_TROZO and tomar < falta:
                    continue    # lo que queda de margen hoy es muy poco: se intenta en otro dia
                trozos, sobra = _asignar(ventanas[d], tomar, con_horas)
                tomado = tomar - sobra
                if usar_tope:
                    cap_restante[d] -= tomado
                falta -= tomado
                for ini, fin, toma in trozos:
                    bloques[d].append({"clave": e["clave"], "item": e["nombre"], "materia": e["materia"],
                                       "minutos": toma, "tipo": e["tipo"], "motivo": _motivo(e),
                                       "inicio": ini, "fin": fin, "fijado": False,
                                       "recomendacion": RECOMENDACION[e["tipo"]]})
            if falta > 0:
                dato = {"clave": e["clave"], "item": e["nombre"], "materia": e["materia"], "fecha": e["fecha"],
                        "faltan_min": falta, "total_min": e["minutos"]}
                (no_alcanza if e["fecha"] or e["vencida"] else sin_espacio).append(dato)

        # Refuerzo: un bloque diario en las materias con menor porcentaje de Moodle, solo con el tiempo que sobra.
        debiles = sorted((m for m, pu in puestos.items() if pu <= REFUERZO_MAX_MATERIAS), key=lambda m: puestos[m])
        nombres = {r["id"]: r["nombre"] for r in conn.execute("SELECT id, nombre FROM materia")}
        puestos_dados = 0
        for d in calendario:
            hueco = next((v for v in ventanas[d] if v[1] - v[0] >= 30), None)
            if not debiles or hueco is None or d in refuerzo_fijo:
                continue
            mid = debiles[puestos_dados % len(debiles)]     # alterna empezando por la de menor porcentaje
            puestos_dados += 1
            trozos, _ = _asignar([hueco], min(REFUERZO_MIN, hueco[1] - hueco[0]), con_horas)
            for ini, fin, toma in trozos:
                bloques[d].append({
                    "clave": f"r:{mid}", "item": f"Refuerzo: {nombres.get(mid, 'materia')}", "materia": None,
                    "minutos": toma, "tipo": "refuerzo", "inicio": ini, "fin": fin, "fijado": False,
                    "motivo": (f"Te sobra tiempo y en Moodle llevas {promedios[mid]:.0f} % en esta materia, "
                               f"una de las más bajas de las que cursas."),
                    "recomendacion": RECOMENDACION["refuerzo"]})
        if con_horas:
            for d in calendario:
                bloques[d].sort(key=lambda b: b["inicio"] or "")

    return {
        "configurado": configurado,
        "dias": [{"fecha": d.isoformat(), "dia": P.DIAS[d.weekday()], "disponible_min": capacidad[d],
                  "usado_min": sum(b["minutos"] for b in bloques[d]), "bloques": bloques[d]} for d in calendario],
        "no_alcanza": no_alcanza,
        "sin_espacio": sin_espacio,
        "por_planificar": [{"clave": e["clave"], "item": e["nombre"], "materia": e["materia"], "minutos": e["minutos"]}
                           for e in en_plan] if not configurado else [],
        "fuera_del_plan": [{"clave": e["clave"], "item": e["nombre"], "fecha": e["fecha"]} for e in fuera],
        "sin_verificar": [{"item": t["tarea"], "materia": t["materia"], "fecha": t["fecha"]} for t in sin_verificar],
        "quitados": [{"clave": e["clave"], "item": e["nombre"], "fecha": e["fecha"]} for e in quitados],
        "con_horas": con_horas,
        "prioridad_moodle": [{"materia": nombres_m, "porcentaje": round(promedios[m], 1), "puesto": pu}
                             for m, pu in sorted(puestos.items(), key=lambda kv: kv[1])
                             for nombres_m in [conn.execute("SELECT nombre FROM materia WHERE id = ?", (m,)).fetchone()[0]]],
        "total_necesario_min": sum(e["minutos"] for e in en_plan),
        "total_disponible_min": sum(capacidad.values()),
        "supuestos": [
            f"Cada tarea se estima en {prefs['minutos_por_tarea']} min y cada evaluación en {prefs['minutos_evaluacion']} min, "
            "ajustados por la dificultad que indicaste (fácil ×0,75, media ×1, difícil ×1,5). Son estimaciones tuyas, no mediciones.",
            "Solo se planifica lo que Moodle confirma como sin entregar y las evaluaciones del calendario; las tareas sin verificar no entran.",
            "Un evento cuenta como evaluación si su nombre lo parece (examen, prueba, parcial…); puedes quitar del plan cualquier elemento.",
        ] + ([
            "Las horas salen de las franjas que indicaste, menos tus clases del ERP y tus actividades; lo que ya pasó hoy no se usa.",
            "La prioridad por notas usa solo el porcentaje de Moodle de tus materias y solo cambia el orden unos días; "
            "si Moodle no informa el total de una materia, no se supone nada.",
        ] if con_horas else []),
    }
