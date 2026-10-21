"""
Cliente de PRUEBA DE CARGA: simula estudiantes usando AcademIA al mismo tiempo.

Cada "usuario virtual" entra (login), y luego durante la etapa repite acciones
al azar con pausas realistas: ver su panel, preguntar al asistente, hacer una
practica en el espacio de estudio, pedir videos, consultar al tutor y
actualizar sus datos. Sube el numero de usuarios por etapas y mide:

  * peticiones por segundo (rendimiento)
  * tiempos de respuesta p50 / p95 / p99
  * % de errores del servidor y % de peticiones rechazadas por proteccion (429/503)
  * memoria / CPU / hilos del servidor (si el servidor de carga tiene psutil)

Genera: resultados_<fecha>/resumen.md (tabla lista para la tesis) y
solicitudes.csv (todos los datos crudos).

Uso (con pruebas/servidor_carga.py corriendo en otra terminal):
    python pruebas/carga.py
    python pruebas/carga.py --etapas 10,25,50,100,200 --segundos 30
    python pruebas/carga.py --url http://mi-servidor:5100 --pensar 0.2,0.8   # mas agresivo
"""
import argparse
import base64
import csv
import os
import random
import statistics
import sys
import threading
import time
from collections import defaultdict
from datetime import datetime

import requests

ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument("--url", default="http://127.0.0.1:5100")
ap.add_argument("--etapas", default="10,25,50,100", help="usuarios simultaneos por etapa, separados por coma")
ap.add_argument("--segundos", type=int, default=20, help="duracion de cada etapa")
ap.add_argument("--pensar", default="1.0,3.0", help="pausa entre acciones de un usuario: min,max segundos")
ap.add_argument("--salida", default=None, help="carpeta de resultados")
ap.add_argument("--timeout", type=float, default=60)
ap.add_argument("--rampa", type=float, default=None, help="segundos en que van entrando los usuarios de una etapa (por defecto, la mitad de la etapa)")
args = ap.parse_args()

URL = args.url.rstrip("/")
PENSAR = tuple(float(x) for x in args.pensar.split(","))
ETAPAS = [int(x) for x in args.etapas.split(",")]
SALIDA = args.salida or os.path.join(os.path.dirname(os.path.abspath(__file__)), "resultados_" + datetime.now().strftime("%Y%m%d_%H%M%S"))
os.makedirs(SALIDA, exist_ok=True)

PREGUNTAS = ["¿Qué tareas tengo pendientes?", "¿Qué nota tengo en cada materia?", "¿Qué clases tengo esta semana?",
             "¿Cuánto necesito para aprobar?", "¿Con qué notas pasé el primer semestre?"]
ACCIONES = [("panel", 30), ("inicio", 5), ("chat", 25), ("estudio", 15), ("tutor", 10), ("videos", 5), ("sync", 5)]

registros = []                # (etapa, accion, estado, ms, t_rel)
entraron = defaultdict(int)   # usuarios que lograron entrar, por etapa
cerrojo = threading.Lock()


def clasificar(codigo):
    if codigo is None:
        return "error"
    if codigo in (429, 503):
        return "limitada"
    return "ok" if codigo < 400 else "error"


def medir(etapa, t_ini, accion, f):
    t0 = time.perf_counter()
    try:
        codigo = f()
    except requests.RequestException:
        codigo = None
    ms = (time.perf_counter() - t0) * 1000
    with cerrojo:
        registros.append((etapa, accion, clasificar(codigo), ms, time.time() - t_ini))
    return codigo


def token_falso(i):
    import json
    b = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")
    jwt = b({"alg": "HS256"}) + "." + b({"exp": int(time.time()) + 7200}) + ".x"
    moodle = "moodlemobile://token=" + base64.b64encode(f"1:::{random.getrandbits(128):032x}".encode()).decode()
    return moodle, jwt


def usuario(etapa, i, t_ini, fin, arranque):
    time.sleep(arranque)
    s = requests.Session()
    moodle, jwt = token_falso(i)
    # Como haria un estudiante: si el servidor dice "estoy ocupado" (429/503), espera y reintenta.
    codigo = None
    for intento in range(4):
        codigo = medir(etapa, t_ini, "login", lambda: s.post(
            f"{URL}/login", data={"moodle_token": moodle, "sga_access_token": jwt, "sga_refresh_token": ""},
            allow_redirects=False, timeout=args.timeout).status_code)
        if codigo == 302:
            break
        if codigo not in (429, 503) or time.time() > fin:
            break
        time.sleep(random.uniform(1, 3))
    if codigo != 302:
        return
    with cerrojo:
        entraron[etapa] += 1
    nombres = [a for a, _ in ACCIONES]
    pesos = [p for _, p in ACCIONES]
    materia = None
    while time.time() < fin:
        accion = random.choices(nombres, pesos)[0]
        post = lambda ruta, cuerpo=None: s.post(f"{URL}{ruta}", json=cuerpo or {}, timeout=args.timeout)
        if accion == "panel":
            medir(etapa, t_ini, "panel", lambda: s.get(f"{URL}/api/panel", timeout=args.timeout).status_code)
        elif accion == "inicio":
            medir(etapa, t_ini, "inicio", lambda: s.get(f"{URL}/", timeout=args.timeout).status_code)
        elif accion == "chat":
            medir(etapa, t_ini, "chat", lambda: post("/api/chat", {"pregunta": random.choice(PREGUNTAS)}).status_code)
        elif accion == "tutor":
            if materia is None:
                materia = _primera_materia(s)
            if materia:
                medir(etapa, t_ini, "tutor", lambda: post("/api/estudio/tutor", {"materia_id": materia, "mensaje": "explícame un concepto"}).status_code)
        elif accion == "videos":
            if materia is None:
                materia = _primera_materia(s)
            if materia:
                medir(etapa, t_ini, "videos", lambda: post("/api/estudio/videos", {"materia_id": materia}).status_code)
        elif accion == "sync":
            medir(etapa, t_ini, "sync", lambda: s.post(f"{URL}/sync", timeout=args.timeout).status_code)
        elif accion == "estudio":
            if materia is None:
                materia = _primera_materia(s)
            if materia and medir(etapa, t_ini, "practica", lambda: post("/api/estudio/practica", {"materia_id": materia, "n": 3}).status_code) == 200:
                for k in range(3):
                    medir(etapa, t_ini, "responder", lambda k=k: post("/api/estudio/responder", {"indice": k, "opcion": random.randint(0, 3)}).status_code)
                    time.sleep(random.uniform(0.3, 0.8))
        time.sleep(random.uniform(*PENSAR))


def _primera_materia(s):
    try:
        r = s.get(f"{URL}/api/estudio/materias", timeout=args.timeout).json()
        return r["materias"][0]["id"] if r.get("materias") else None
    except Exception:
        return None


# ------------------------------------------------------------ metricas del servidor
metricas = []


def sondeo(etapa, detener):
    while not detener.is_set():
        try:
            m = requests.get(f"{URL}/__metricas", timeout=5).json()
            metricas.append((etapa, m))
        except Exception:
            pass
        detener.wait(2)


def pct(valores, p):
    if not valores:
        return float("nan")
    v = sorted(valores)
    return v[min(len(v) - 1, int(round(p / 100 * (len(v) - 1))))]


def main():
    try:
        requests.get(f"{URL}/login", timeout=5)
    except requests.RequestException as e:
        sys.exit(f"No se pudo conectar a {URL}. ¿Está corriendo pruebas/servidor_carga.py?\n  {e}")
    print(f"Etapas: {ETAPAS} usuarios · {args.segundos} s cada una · pausa {PENSAR} s\nResultados en: {SALIDA}\n")
    filas = []
    for n in ETAPAS:
        t_ini = time.time()
        fin = t_ini + args.segundos
        rampa = args.rampa if args.rampa is not None else args.segundos * 0.5
        detener = threading.Event()
        threading.Thread(target=sondeo, args=(n, detener), daemon=True).start()
        hilos = [threading.Thread(target=usuario, args=(n, i, t_ini, fin, i * rampa / n), daemon=True) for i in range(n)]
        for h in hilos:
            h.start()
        for h in hilos:
            h.join(timeout=args.segundos + args.timeout + 10)
        detener.set()
        reg = [r for r in registros if r[0] == n]
        sin_login = [r for r in reg if r[1] != "login"]
        oks = [r[3] for r in sin_login if r[2] == "ok"]
        logins = [r[3] for r in reg if r[1] == "login" and r[2] == "ok"]
        total = len(sin_login)
        err = sum(1 for r in reg if r[2] == "error")                 # 5xx, caidas, tiempos agotados (incluye login)
        lim = sum(1 for r in sin_login if r[2] == "limitada")
        login_ocupado = sum(1 for r in reg if r[1] == "login" and r[2] == "limitada")
        mets = [m for e, m in metricas if e == n]
        fila = {
            "usuarios": n, "peticiones": total, "rps": total / args.segundos,
            "p50": pct(oks, 50), "p95": pct(oks, 95), "p99": pct(oks, 99),
            "login_p95": pct(logins, 95), "errores_pct": 100 * err / max(1, len(reg)),
            "entraron": entraron[n], "login_ocupado": login_ocupado,
            "limitadas_pct": 100 * lim / max(1, total),
            "rss_mb": max((m.get("rss_mb", 0) for m in mets), default=0),
            "cpu_pct": max((m.get("cpu_pct", 0) for m in mets), default=0),
            "hilos": max((m.get("hilos", 0) for m in mets), default=0),
        }
        filas.append(fila)
        print(f"  {n:>4} usuarios | {fila['rps']:6.1f} req/s | p50 {fila['p50']:6.0f} ms | p95 {fila['p95']:6.0f} ms | "
              f"p99 {fila['p99']:6.0f} ms | errores {fila['errores_pct']:5.1f}% | rechazadas {fila['limitadas_pct']:5.1f}% | entraron {fila['entraron']}/{n}"
              + (f" | RAM {fila['rss_mb']:.0f} MB | CPU {fila['cpu_pct']:.0f}%" if fila["rss_mb"] else ""))
        if fila["errores_pct"] > 50:
            print("  El servidor colapsó (más de 50 % de errores); se detiene la prueba.")
            break
        time.sleep(3)          # respiro entre etapas

    guardar(filas)


def guardar(filas):
    with open(os.path.join(SALIDA, "solicitudes.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["usuarios_etapa", "accion", "estado", "ms", "segundo"])
        w.writerows((e, a, s, f"{ms:.1f}", f"{t:.2f}") for e, a, s, ms, t in registros)

    ultima = filas[-1]["usuarios"]
    por_accion = defaultdict(list)
    for e, a, s, ms, _ in registros:
        if e == ultima and s == "ok":
            por_accion[a].append(ms)
    sano = [f for f in filas if f["p95"] < 2000 and f["errores_pct"] < 1 and f["entraron"] == f["usuarios"]]
    capacidad = max((f["usuarios"] for f in sano), default=0)

    L = ["# Resultados de la prueba de carga de AcademIA", "",
         f"- Fecha: {datetime.now():%Y-%m-%d %H:%M}", f"- Servidor: `{URL}`",
         f"- Duración por etapa: {args.segundos} s · pausa entre acciones: {PENSAR[0]}–{PENSAR[1]} s",
         f"- Mezcla de acciones: " + ", ".join(f"{a} {p}%" for a, p in ACCIONES), "",
         "## Rendimiento por número de usuarios simultáneos", "",
         "| Usuarios | Entraron | Peticiones | Req/s | p50 (ms) | p95 (ms) | p99 (ms) | Login p95 (ms) | Errores % | Rechazadas % | RAM (MB) | CPU % |",
         "|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for f in filas:
        L.append(f"| {f['usuarios']} | {f['entraron']} | {f['peticiones']} | {f['rps']:.1f} | {f['p50']:.0f} | {f['p95']:.0f} | {f['p99']:.0f} | "
                 f"{f['login_p95']:.0f} | {f['errores_pct']:.1f} | {f['limitadas_pct']:.1f} | {f['rss_mb'] or '—'} | {f['cpu_pct'] or '—'} |")
    L += ["", f"**Capacidad estimada:** hasta **{capacidad} usuarios simultáneos** con p95 < 2 s, menos de 1 % de errores y todos los usuarios entrando."
          if capacidad else "**Capacidad estimada:** ninguna etapa cumplió p95 < 2 s con menos de 1 % de errores.", "",
          f"## Tiempo de respuesta por acción (etapa de {ultima} usuarios, solo respuestas correctas)", "",
          "| Acción | Peticiones | p50 (ms) | p95 (ms) |", "|---|---:|---:|---:|"]
    for a, v in sorted(por_accion.items(), key=lambda kv: -pct(kv[1], 95)):
        L.append(f"| {a} | {len(v)} | {pct(v, 50):.0f} | {pct(v, 95):.0f} |")
    L += ["", "## Cómo leer estos números", "",
          "- **p95**: el 95 % de las peticiones respondió en ese tiempo o menos. Es la cifra que siente el usuario en un mal momento.",
          "- **Errores**: respuestas 5xx, caídas o tiempos agotados. Deben ser 0 en una operación sana.",
          "- **Rechazadas**: respuestas 429/503 de la protección del servidor (límites de uso y cupos). No son fallos: es el servidor diciendo \"estoy ocupado\" en lugar de caerse.",
          "- **Entraron**: usuarios que lograron iniciar sesión (reintentan hasta 4 veces cuando el servidor está ocupado, como haría un estudiante).",
          "- Las latencias de Moodle, ERP e IA son **simuladas** (ver cabecera de `servidor_carga.py`); aquí se mide la capacidad de la aplicación, no la de UNEMI ni la de Anthropic.",
          "- Cliente y servidor comparten máquina, así que el cliente también consume CPU: en un servidor dedicado los números suelen mejorar."]
    with open(os.path.join(SALIDA, "resumen.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    print(f"\nListo. Resumen para la tesis: {os.path.join(SALIDA, 'resumen.md')}")


if __name__ == "__main__":
    main()
