"""
Servidor de PRUEBA DE CARGA de AcademIA.

Arranca la aplicacion REAL (rutas, sesiones cifradas, base de datos SQLite,
limites, cupos, espacio de estudio) pero reemplaza los servicios externos por
simulaciones con latencia configurable:

    Moodle / ERP (sincronizacion)  ->  --sync-seg   (por defecto 1.5 s)
    Modelo de IA (chat, practica)  ->  --ia-seg     (por defecto 1.2 s)

Asi se mide la capacidad de la APLICACION (hilos, SQLite, memoria, cupos) sin
llamar a UNEMI ni gastar tokens de la API. No hay ninguna puerta trasera en el
codigo de produccion: todo el reemplazo ocurre solo en este script.

Uso:
    python pruebas/servidor_carga.py                 # 48 hilos, puerto 5100
    python pruebas/servidor_carga.py --hilos 64 --sync-seg 3
    python pruebas/servidor_carga.py --con-limites   # deja activos los limites de uso

Despues, en otra terminal:  python pruebas/carga.py
"""
import argparse
import json
import os
import random
import sys
import tempfile
import threading
import time
from datetime import datetime

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
sys.path.insert(0, os.path.join(RAIZ, "web"))

ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument("--puerto", type=int, default=5100)
ap.add_argument("--host", default="127.0.0.1")
ap.add_argument("--hilos", type=int, default=int(os.getenv("THREADS", "48")), help="hilos de Waitress")
ap.add_argument("--sync-seg", type=float, default=1.5, help="latencia simulada de Moodle+ERP")
ap.add_argument("--ia-seg", type=float, default=1.2, help="latencia simulada del modelo de IA")
ap.add_argument("--con-limites", action="store_true", help="no relajar los limites de uso (prueba de limites)")
ap.add_argument("--db", default=None, help="archivo SQLite (por defecto uno temporal)")
args = ap.parse_args()

# --- Entorno ANTES de importar la app ---
os.environ["DB_PATH"] = args.db or os.path.join(tempfile.mkdtemp(prefix="academia_carga_"), "carga.db")
os.environ["ANTHROPIC_API_KEY"] = "simulada"
os.environ.setdefault("SESSION_ENC_KEY", __import__("cryptography.fernet", fromlist=["Fernet"]).Fernet.generate_key().decode())
if not args.con_limites:
    for nombre in ("LOGIN", "CHAT", "ESTUDIO", "SYNC", "GENERAL"):
        os.environ[f"LIMITE_{nombre}"] = "1000000/60"
    os.environ["LIMITE_DIARIO_IA"] = "100000000"

import app as A                      # noqa: E402  (la aplicacion real)
import limites                       # noqa: E402
from core.assistant import AssistantLLM          # noqa: E402
from core.estudio import Estudio                 # noqa: E402
from core.query_engine import QueryEngine        # noqa: E402
from db.setup import get_connection              # noqa: E402
from sga.client import SGAClient                 # noqa: E402

try:
    import psutil
    _proceso = psutil.Process()
except ImportError:                  # opcional: sin psutil no se mide CPU ni memoria
    psutil = None


def espera(media: float):
    time.sleep(max(0.0, random.gauss(media, media * 0.25)))


# ---------------------------------------------------------------- Moodle / ERP simulados
class MoodleSimulado:
    def __init__(self, base_url=None, token=None, **kw):
        self.token = token or ""

    def get_site_info(self):
        espera(args.sync_seg * 0.2)
        uid = int(self.token[:8] or "0", 16) % 900000 + 1000 if self.token else 1000
        return {"userid": uid, "fullname": f"ESTUDIANTE {uid}"}


class SGASimulado(SGAClient):
    def cambiar_a_perfil_regular(self):
        return False

    def get_alumno_id_automatico(self):
        return "1"

    def refresh_access_token(self):
        return False


def sync_simulado(estudiante_id, moodle_client=None, sga_client=None, sga_alumno_id=None, **kw):
    """Simula la sincronizacion: latencia de red + escrituras reales en SQLite."""
    espera(args.sync_seg * 0.8)
    conn = get_connection()
    try:
        ahora = datetime.now().isoformat(timespec="seconds")
        conn.execute("INSERT OR IGNORE INTO estudiante(id, nombre) VALUES (?, ?)", (estudiante_id, f"ESTUDIANTE {estudiante_id}"))
        conn.execute("UPDATE estudiante SET fecha_sincronizacion = ? WHERE id = ?", (ahora, estudiante_id))
        for i, n in enumerate(("CÁLCULO", "FÍSICA", "PROGRAMACIÓN", "ÁLGEBRA", "REDES"), 1):
            conn.execute("INSERT OR IGNORE INTO materia(id, nombre, semestre, moodle_course_id) VALUES (?, ?, ?, ?)", (i, n, i, 9000 + i))
        for mid in random.sample(range(1, 6), 3):
            conn.execute("""INSERT INTO matricula(estudiante_id, materia_id, periodo, estado, nota_final, asistencia)
                            VALUES (?, ?, 'PRUEBA', 'en_curso', ?, ?)
                            ON CONFLICT(estudiante_id, materia_id, periodo)
                            DO UPDATE SET nota_final = excluded.nota_final, asistencia = excluded.asistencia""",
                         (estudiante_id, mid, round(random.uniform(40, 95), 1), round(random.uniform(70, 100))))
            matricula_id = conn.execute("SELECT id FROM matricula WHERE estudiante_id=? AND materia_id=? AND periodo='PRUEBA'",
                                        (estudiante_id, mid)).fetchone()[0]
            conn.execute("DELETE FROM componente_nota WHERE matricula_id = ?", (matricula_id,))
            for k in range(1, 7):
                conn.execute("INSERT INTO componente_nota(matricula_id, nombre_item, calificacion, porcentaje) VALUES (?, ?, ?, ?)",
                             (matricula_id, f"Total N{k}", f"{random.uniform(3, 10):.2f}", "20%"))
            conn.execute("INSERT OR IGNORE INTO tarea(materia_id, moodle_assign_id, nombre, fecha_entrega) VALUES (?, ?, ?, ?)",
                         (mid, 100 + mid, f"Tarea {mid}", "2099-01-01T12:00:00"))
        for plat in ("moodle", "sga"):
            conn.execute("INSERT INTO sesion_extraccion(estudiante_id, plataforma, fecha, estado, detalle) VALUES (?, ?, ?, 'exitosa', 'simulado')",
                         (estudiante_id, plat, ahora))
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------- IA simulada
class AsistenteSimulado(AssistantLLM):
    """Usa el QueryEngine REAL (consultas a SQLite); solo la llamada al modelo es simulada."""

    def __init__(self, estudiante_id: int = 1, **kw):
        self.engine = QueryEngine(estudiante_id=estudiante_id)
        self.historial = []
        self.herramientas_usadas = []
        self.acciones = []
        self.lock = threading.Lock()

    def preguntar(self, pregunta: str) -> str:
        self.herramientas_usadas = ["tareas_pendientes", "materias_actuales"]
        espera(args.ia_seg)
        t = self.engine.tareas_pendientes()
        m = self.engine.materias_actuales()
        self.historial = self.historial[-6:] + [{"role": "user", "content": pregunta}]
        return f"(simulado) {str(t)[:80]} | {str(m)[:80]}"


class _Bloque:
    def __init__(self, tipo, **kw):
        self.type = tipo
        self.__dict__.update(kw)


class ClienteIASimulado:
    """Imita client.messages.create() de Anthropic con latencia fija."""

    class _Mensajes:
        def create(self, **kw):
            espera(args.ia_seg)
            herr = kw.get("tools") or []
            if herr and herr[0]["name"] == "crear_practica":
                qs = [{"subtema": f"Tema {i}", "pregunta": f"Pregunta {i}", "opciones": ["a", "b", "c", "d"],
                       "correcta": 0, "explicacion": "Porque sí."} for i in range(10)]
                return type("R", (), {"content": [_Bloque("tool_use", name="crear_practica", input={"preguntas": qs})]})()
            if herr and herr[0]["name"] == "sugerir_busquedas":
                bs = [{"busqueda": f"clase de ejemplo {i}", "enfoque": "intro"} for i in range(4)]
                return type("R", (), {"content": [_Bloque("tool_use", name="sugerir_busquedas", input={"busquedas": bs})]})()
            return type("R", (), {"content": [_Bloque("text", text="Respuesta simulada del tutor.")]})()

    def __init__(self):
        self.messages = self._Mensajes()


A.MoodleClient = MoodleSimulado
A.SGAClient = SGASimulado
A.sync_estudiante = sync_simulado
A.AssistantLLM = AsistenteSimulado
A.Estudio = lambda estudiante_id: Estudio(estudiante_id, client=ClienteIASimulado())


# ---------------------------------------------------------------- metricas del servidor
@A.app.route("/__metricas")
def metricas():
    datos = {
        "hilos": threading.active_count(),
        "sesiones_asistente": len(A.ASSISTANTS),
        "sesiones_estudio": len(A.ESTUDIOS),
        "cupo_sync": {"max": limites.cupo_sync.n, "en_uso": limites.cupo_sync.en_uso, "esperando": limites.cupo_sync.esperando, "rechazadas": limites.cupo_sync.rechazadas},
        "cupo_ia": {"max": limites.cupo_ia.n, "en_uso": limites.cupo_ia.en_uso, "esperando": limites.cupo_ia.esperando, "rechazadas": limites.cupo_ia.rechazadas},
        "ia_usadas": limites.presupuesto_ia.usadas,
    }
    if psutil:
        datos["rss_mb"] = round(_proceso.memory_info().rss / 1048576, 1)
        datos["cpu_pct"] = _proceso.cpu_percent(interval=None)
    return json.dumps(datos), 200, {"Content-Type": "application/json"}


if __name__ == "__main__":
    from waitress import serve
    limites.advertir_hilos(args.hilos)
    print("=" * 66)
    print(" SERVIDOR DE PRUEBA DE CARGA (servicios externos SIMULADOS)")
    print(f" http://{args.host}:{args.puerto}   hilos={args.hilos}   sync={args.sync_seg}s   ia={args.ia_seg}s")
    print(f" cupos: sync={limites.cupo_sync.n}  ia={limites.cupo_ia.n}   limites de uso: {'ACTIVOS' if args.con_limites else 'relajados'}")
    print(f" base de datos: {os.environ['DB_PATH']}")
    print(" NO uses este servidor con datos reales: acepta cualquier token.")
    print("=" * 66)
    serve(A.app, host=args.host, port=args.puerto, threads=args.hilos,
          connection_limit=int(os.getenv("CONNECTION_LIMIT", "1000")), channel_timeout=60)
