"""Hilo en segundo plano: atiende al bot de Telegram y manda los recordatorios del plan.

Solo se inicia si existe TELEGRAM_BOT_TOKEN en el .env. Usa solo la base de datos (no necesita ningun
token de Moodle ni del ERP). Funciona mientras la app este encendida; los avisos usan lo ultimo que
se sincronizo."""
import threading
import time

from core import telegram
from db.setup import get_connection


class Trabajador(threading.Thread):
    def __init__(self, token: str):
        super().__init__(daemon=True, name="telegram-recordatorios")
        self.token = token
        self.parar = threading.Event()
        self.offset = None

    def un_ciclo(self, espera: int = 8):
        conn = get_connection()
        try:
            for u in telegram.actualizaciones(self.token, self.offset, espera):
                self.offset = u["update_id"] + 1
                r = telegram.responder(conn, u.get("message"))
                if r:
                    try:
                        telegram.enviar(self.token, r[0], r[1])
                    except telegram.TelegramError as e:
                        print(f"[telegram] No se pudo responder: {e}")
            telegram.enviar_pendientes(conn, self.token)
        finally:
            conn.close()

    def run(self):
        while not self.parar.is_set():       # si no hay red o el token falla, reintenta cada 30 s
            try:
                usuario = telegram.preparar_bot(self.token)
                print(f"[telegram] Recordatorios activos con el bot @{usuario}.")
                break
            except telegram.TelegramError as e:
                print(f"[telegram] No se pudo iniciar el bot ({e}). Revisa TELEGRAM_BOT_TOKEN; reintento en 30 s.")
                self.parar.wait(30)
        while not self.parar.is_set():
            try:
                self.un_ciclo()
            except telegram.TelegramError as e:
                print(f"[telegram] {e}")
                time.sleep(5)
            except Exception as e:
                print(f"[telegram] Error inesperado: {type(e).__name__}")
                time.sleep(5)
