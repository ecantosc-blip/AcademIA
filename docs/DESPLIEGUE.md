# Despliegue y escalabilidad de AcademIA

## 1. Antes de abrirla a otras personas

1. **Permiso.** La app recibe tokens de Moodle y del ERP de otros estudiantes y guarda sus datos académicos.
   Confirma con tu tutor de tesis que la universidad lo permite y pide a cada participante un consentimiento escrito simple.
2. **Gasto.** Cada pregunta al chat o práctica del espacio de estudio usa tu `ANTHROPIC_API_KEY`.
   Pon un límite de gasto mensual en console.anthropic.com. Además la app corta sola al llegar a `LIMITE_DIARIO_IA` consultas por día.
3. **Acceso desde el servidor.** UNEMI podría bloquear peticiones que lleguen desde servidores en el extranjero.
   Pruébalo desde el primer día de despliegue (entra con tus tokens y revisa que se lean tus notas).

## 2. Opción A: pruebas con compañeros (gratis, sin contratar nada)

La app corre en tu computadora y Cloudflare Tunnel le da un enlace con HTTPS.

```powershell
winget install --id Cloudflare.cloudflared          # una sola vez
$env:COOKIE_SECURE = "1"; $env:TRUST_PROXY = "1"    # cookies seguras y límites por IP real
python web\app.py                                   # terminal 1
cloudflared tunnel --url http://localhost:5000      # terminal 2: imprime https://algo.trycloudflare.com
```

Comparte ese enlace. Solo funciona mientras tu computadora esté encendida y las dos terminales abiertas.

## 3. Opción B: siempre disponible (Render)

1. Sube el proyecto a GitHub (el `.gitignore` ya excluye `.env`, las bases de datos y `.session_key`).
2. En Render: **New > Blueprint** y elige el repositorio; usa `render.yaml`.
3. En el panel del servicio escribe `ANTHROPIC_API_KEY`. El resto de variables ya vienen en `render.yaml`.
4. Render da una URL con HTTPS. El disco persistente (`/data`) guarda la base de datos entre reinicios.

Funciona igual en Railway, Fly.io o un VPS con Docker: `docker build -t academia .` y
`docker run -p 5000:5000 -v academia-datos:/data --env-file .env -e COOKIE_SECURE=1 academia`.
Vercel y Netlify **no** sirven para esta app (no tienen disco persistente ni procesos de larga duración).

## 4. Variables de entorno

| Variable | Por defecto | Para qué |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | Llave de la API (obligatoria) |
| `SESSION_ENC_KEY` | archivo `.session_key` | Secreto para cifrar las sesiones. En un servidor, defínelo siempre |
| `COOKIE_SECURE` | 0 | Poner 1 si se sirve por HTTPS |
| `TRUST_PROXY` | 0 | Poner 1 detrás de Render/Cloudflare/nginx para que los límites usen la IP real |
| `DB_PATH` | `./unemi_assistant.db` | Archivo SQLite (en el hosting, dentro del disco persistente) |
| `THREADS` | 48 | Peticiones atendidas a la vez |
| `LIMITE_LOGIN` `LIMITE_CHAT` `LIMITE_ESTUDIO` `LIMITE_SYNC` `LIMITE_GENERAL` | ver `.env.example` | Límites por ventana, formato `N/segundos` |
| `LIMITE_DIARIO_IA` | 1000 | Tope global de consultas a la IA por día |
| `CUPO_SYNC` `CUPO_IA` (+ `_COLA`) | 8 / 16 (cola 8) | Operaciones lentas simultáneas y su sala de espera |

## 5. Protecciones incluidas

- **Límites por ventana** (login por IP, chat/estudio/sync por sesión) con respuesta 429 y tiempo de espera.
- **Presupuesto diario de IA**: tope global; al agotarse, el chat responde con un aviso claro.
- **Cupos con sala de espera acotada** para lo lento (sincronizar con Moodle/ERP, llamar al modelo): si hay demasiados a la vez, el exceso recibe 503
  al instante y las páginas baratas (panel, responder) siguen rápidas.
- **Cabeceras de seguridad** (CSP, X-Frame-Options, nosniff, no-store en datos personales, HSTS con HTTPS).
- **Memoria acotada** (`MAX_SESIONES_MEMORIA`) y **una conexión SQLite por hilo** (no por sesión), con modo WAL.

## 6. Escalabilidad: qué se puede esperar

La app corre en **un solo proceso** con hilos. Eso es lo que mide la prueba de carga y lo que conviene decir en la tesis:

- **Escala bien hacia arriba** (más CPU/hilos) para cientos de estudiantes con uso normal: las lecturas son consultas pequeñas a SQLite en modo WAL.
- **Los límites reales vienen de afuera**: la latencia de UNEMI al sincronizar (varios segundos) y la de la IA. Por eso hay cupos: protegen también a UNEMI de ráfagas.
- **No escala horizontalmente tal cual** (varias copias detrás de un balanceador): las conversaciones del asistente viven en la memoria del proceso y SQLite es un archivo local.
  Para crecer más allá de un servidor: PostgreSQL en lugar de SQLite, Redis para el estado de conversación y varias réplicas. Es trabajo futuro razonable.

## 7. Medir cuántos usuarios soporta

Ver `pruebas/README.md`. Resumen:

```powershell
pip install -r pruebas\requirements-pruebas.txt
python pruebas\servidor_carga.py                                    # terminal 1
python pruebas\carga.py --etapas 10,25,50,100,200 --segundos 30     # terminal 2
```

Genera `pruebas\resultados_<fecha>\resumen.md` con la tabla lista para el capítulo de resultados.
