"""
Proteccion del servidor frente al uso excesivo (accidental o malicioso).

Tres mecanismos, todos configurables por variables de entorno (.env):

1. Limitador por ventana deslizante: "como maximo N peticiones en S segundos"
   por clave (la sesion del usuario, o su IP para el login). Si se pasa, la
   peticion recibe 429 con el tiempo de espera.
2. Presupuesto diario de la IA: tope global de consultas al modelo por dia, para
   que el gasto de la API key nunca se dispare, pase lo que pase.
3. Cupos de concurrencia: cuantas operaciones LENTAS (sincronizar con
   Moodle/ERP, llamar al modelo) pueden correr a la vez. Sin esto, unas pocas
   sincronizaciones lentas ocupan todos los hilos del servidor y nadie mas
   puede ni abrir la pagina. Con cupos, el servidor degrada con elegancia:
   responde "estoy ocupado, intenta en un momento" y sigue atendiendo lo demas.

Todo vive en memoria del proceso (suficiente para un servidor de un proceso, que
es como corre esta app; ver README, seccion Escalabilidad).
"""
import os
import threading
import time
from collections import defaultdict, deque
from contextlib import contextmanager
from datetime import date


def _leer_limite(nombre: str, por_defecto: str) -> tuple:
    """Lee 'N/S' (N peticiones cada S segundos) de una variable de entorno."""
    texto = os.getenv(nombre, por_defecto)
    try:
        n, s = texto.split("/")
        return max(1, int(n)), max(1, int(s))
    except ValueError:
        n, s = por_defecto.split("/")
        return int(n), int(s)


# Valores por defecto pensados para un uso normal de estudiante (con holgura).
LIMITES = {
    "login":   ("LIMITE_LOGIN",   "10/600"),    # por IP: 10 intentos cada 10 min
    "login_usuario": ("LIMITE_LOGIN_USUARIO", "5/600"),   # por usuario de Moodle: frena adivinar claves
    "chat":    ("LIMITE_CHAT",    "20/60"),     # por sesion
    "estudio": ("LIMITE_ESTUDIO", "20/300"),    # practica, videos y tutor, por sesion
    "sync":    ("LIMITE_SYNC",    "4/300"),     # por sesion: 4 actualizaciones cada 5 min
    "carnet":  ("LIMITE_CARNET",  "6/300"),     # por sesion: descargas del carne
    "general": ("LIMITE_GENERAL", "300/60"),    # por IP: tope a cualquier inundacion
}


class Limitador:
    def __init__(self):
        self._lock = threading.Lock()
        self._eventos = defaultdict(deque)
        self._ultima_limpieza = time.monotonic()

    def permitir(self, clave: str, maximo: int, ventana: int, ahora: float = None) -> tuple:
        """Devuelve (True, 0) si cabe, o (False, segundos_de_espera) si se paso."""
        ahora = time.monotonic() if ahora is None else ahora
        with self._lock:
            cola = self._eventos[clave]
            while cola and cola[0] <= ahora - ventana:
                cola.popleft()
            if len(cola) >= maximo:
                return False, max(1, int(cola[0] + ventana - ahora) + 1)
            cola.append(ahora)
            if ahora - self._ultima_limpieza > 300:     # evita que el diccionario crezca sin fin
                self._ultima_limpieza = ahora
                for k in [k for k, c in self._eventos.items() if not c or c[-1] <= ahora - 3600]:
                    del self._eventos[k]
            return True, 0

    def reiniciar(self):
        with self._lock:
            self._eventos.clear()


class PresupuestoIA:
    """Tope global de consultas a la IA por dia (se reinicia a medianoche)."""

    def __init__(self):
        self._lock = threading.Lock()
        self._dia = date.today()
        self._usadas = 0

    @property
    def maximo(self) -> int:
        try:
            return max(0, int(os.getenv("LIMITE_DIARIO_IA", "1000")))
        except ValueError:
            return 1000

    def consumir(self) -> bool:
        with self._lock:
            hoy = date.today()
            if hoy != self._dia:
                self._dia, self._usadas = hoy, 0
            if self._usadas >= self.maximo:
                return False
            self._usadas += 1
            return True

    @property
    def usadas(self) -> int:
        return self._usadas


class Ocupado(Exception):
    """No hay cupo libre para esta operacion lenta en este momento."""


class Cupo:
    """
    Deja pasar hasta N operaciones lentas a la vez, con una SALA DE ESPERA acotada.

    Por que acotada: cada peticion que espera ocupa un hilo del servidor. Si
    esperaran todas, las operaciones lentas acabarian ocupando todos los hilos y
    hasta cargar el panel (barato) quedaria en cola. Con una sala de espera de
    tamano fijo, el exceso recibe "ocupado" al instante y siempre quedan hilos
    libres para lo demas. Regla para elegir THREADS (ver README):
        THREADS >= suma de (cupo + sala de espera) de todos los cupos + 8
    """

    def __init__(self, nombre_env: str, por_defecto: int, cola_por_defecto: int):
        def _entero(nombre, defecto):
            try:
                return max(0, int(os.getenv(nombre, str(defecto))))
            except ValueError:
                return defecto
        self.n = max(1, _entero(nombre_env, por_defecto))
        self.cola = _entero(nombre_env + "_COLA", cola_por_defecto)
        self._sem = threading.BoundedSemaphore(self.n)
        self._lock = threading.Lock()
        self.en_uso = 0
        self.esperando = 0
        self.rechazadas = 0

    @contextmanager
    def tomar(self, espera: float = 5.0):
        if not self._sem.acquire(blocking=False):          # sin cupo libre: sala de espera
            with self._lock:
                if self.esperando >= self.cola:
                    self.rechazadas += 1
                    raise Ocupado()
                self.esperando += 1
            try:
                conseguido = self._sem.acquire(timeout=espera)
            finally:
                with self._lock:
                    self.esperando -= 1
            if not conseguido:
                with self._lock:
                    self.rechazadas += 1
                raise Ocupado()
        with self._lock:
            self.en_uso += 1
        try:
            yield
        finally:
            with self._lock:
                self.en_uso -= 1
            self._sem.release()


limitador = Limitador()
presupuesto_ia = PresupuestoIA()
cupo_sync = Cupo("CUPO_SYNC", 8, 8)     # sincronizaciones simultaneas (llaman a Moodle y al ERP)
cupo_ia = Cupo("CUPO_IA", 16, 8)        # llamadas simultaneas al modelo


def hilos_recomendados() -> int:
    """Hilos minimos para que las operaciones lentas nunca dejen sin hilos al resto."""
    return cupo_sync.n + cupo_sync.cola + cupo_ia.n + cupo_ia.cola + 8


def advertir_hilos(hilos: int):
    minimo = hilos_recomendados()
    if hilos < minimo:
        print(f"AVISO: THREADS={hilos} es menor que {minimo} (cupos + salas de espera + 8). "
              f"Con mucha carga, las operaciones lentas podrian dejar sin hilos al resto. "
              f"Sube THREADS o baja CUPO_SYNC / CUPO_IA.")


def revisar(tipo: str, clave: str) -> tuple:
    """Aplica el limite de `tipo` a `clave`. Devuelve (True, 0) o (False, espera)."""
    nombre_env, defecto = LIMITES[tipo]
    maximo, ventana = _leer_limite(nombre_env, defecto)
    return limitador.permitir(f"{tipo}:{clave}", maximo, ventana)
