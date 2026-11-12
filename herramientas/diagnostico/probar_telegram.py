"""Diagnostico de la conexion con Telegram. No imprime el token.

Uso (desde la carpeta del proyecto, con el venv activo):
    python herramientas/diagnostico/probar_telegram.py
"""
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from dotenv import load_dotenv  # noqa: E402

load_dotenv()
token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
print("1. Token en .env:", "SI (largo %d)" % len(token) if token else "NO -> falta TELEGRAM_BOT_TOKEN")
if token and (":" not in token or " " in token):
    print("   AVISO: el token tiene forma rara (debe ser como 123456789:AAE... sin espacios ni comillas).")

from core import telegram as T  # noqa: E402
print("2. telegram.py nuevo (con respaldo curl):", "SI" if hasattr(T, "_por_curl") else "NO -> falta reemplazar core/telegram.py")
exe = shutil.which("curl.exe") or shutil.which("curl")
print("3. curl encontrado:", exe or "NO")
if not token:
    sys.exit(1)

import requests  # noqa: E402
try:
    r = requests.post(f"{T.API}/bot{token}/getMe", json={}, timeout=10)
    print("4. Python directo: responde", r.status_code, "-> token", "VALIDO" if r.status_code == 200 else "RECHAZADO (revisa el token)")
except requests.RequestException as e:
    print("4. Python directo: falla (%s)" % type(e).__name__)

if hasattr(T, "_por_curl") and exe:
    cuerpo = T._por_curl(f"{T.API}/bot{token}/getMe", {}, 15)
    print("5. Por curl:", ("responde: " + cuerpo[:80].replace(token, "***")) if cuerpo else "falla (sin respuesta)")
    if not cuerpo:
        r = subprocess.run([exe, "-sS", "-m", "10", "-o", "NUL" if os.name == "nt" else "/dev/null", "-w", "%{http_code}",
                            "https://api.telegram.org/"], capture_output=True, text=True)
        print("   curl a la raiz de Telegram: codigo", r.stdout, "| error:", (r.stderr or "-").strip()[:120])
try:
    print("6. preparar_bot:", "OK, usuario @" + str(T.preparar_bot(token)))
except T.TelegramError as e:
    print("6. preparar_bot falla:", e)
