# Prototipo: Asistente Inteligente UNEMI

Prototipo de validación técnica. Objetivo de esta fase: confirmar que el
flujo completo (conexión a Moodle + SGA -> base de datos unificada ->
respuesta a preguntas) funciona de punta a punta, ANTES de invertir tiempo
en la capa de LLM/RAG y en el marco teórico completo.

**IMPORTANTE - Seguridad:** Este proyecto corre 100% en tu computadora.
La app web usa **autenticación por token**: nunca pide ni guarda
contraseñas. Los tokens de sesión que pegas en el login se guardan
cifrados y se descartan al cerrar sesión o al vencer (8 horas). El archivo
`.env` y el archivo `.session_key` están en `.gitignore`: nunca los subas
a GitHub ni los compartas con nadie.

## Requisitos previos

- Python 3.10 o superior instalado.
- Tus credenciales del SGA y de Aulas Virtuales (las mismas de siempre).

## Pasos para ejecutar

### 1. Instalar dependencias

```bash
cd unemi-assistant
python -m venv venv
source venv/bin/activate      # En Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configurar credenciales

```bash
cp .env.example .env
```

Abre `.env` con un editor de texto y completa:
- `MOODLE_TOKEN` (recomendado, sin contraseña: el valor del enlace `moodlemobile://token=...`). Solo para pruebas por consola heredadas se admite `MOODLE_USERNAME` y `MOODLE_PASSWORD`; la app web nunca los usa.
- Para el SGA, el login SÍ tiene protección anti-bot, así que hay que
  obtener el token manualmente la primera vez:
  1. Entra a `https://sgaestudiante.unemi.edu.ec` en Chrome/Firefox.
  2. Abre DevTools (F12) -> pestaña Network.
  3. Inicia sesión normalmente.
  4. Busca la petición `login.json`, pestaña Response.
  5. Copia el valor de `"access"` y pégalo en `SGA_ACCESS_TOKEN` en tu `.env`.
  6. Copia el valor de `"refresh"` y pégalo en `SGA_REFRESH_TOKEN`.
  7. Copia el ID de alumno que aparece en la URL cuando consultas notas
     (ej. `.../notas/ESTE_ID/`) y pégalo en `SGA_ALUMNO_ID`.

### 3. Inicializar la base de datos

```bash
python db/setup.py
```

Esto crea el archivo `unemi_assistant.db` con las tablas definidas en
`db/schema.sql`.

### 4. Probar el conector de Moodle (el más simple, empieza por aquí)

```bash
python moodle/client.py
```

Si todo funciona, deberías ver tu nombre, tus cursos y tus próximos eventos
impresos en la consola. **Este es el primer punto de validación real.**

### 5. Probar el conector del SGA

```bash
python sga/client.py
```

**Nota:** el endpoint de refresh del token (`refresh_access_token` en
`sga/client.py`) todavía no está confirmado — hay que inspeccionarlo en
DevTools la primera vez que el access_token expire. Si el script falla
con error 401, ese es el próximo paso a investigar juntos.

### 6. Sincronizar datos a la base unificada (Moodle + SGA)

```bash
python core/sync_all.py
```

Esto trae tus cursos, tareas, eventos y calificaciones detalladas de Moodle,
y tu nota final + asistencia oficial del SGA, y lo guarda todo en la base
de datos local unificada. (El script anterior, `sync_moodle.py`, se retiró:
`sync_all.py` hace todo lo que hacía y más, y está cubierto por las pruebas).

### 7. Probar el motor de consultas (modo chat, basado en reglas - sin IA todavía)

```bash
python core/query_engine.py
```

Preguntas soportadas en esta versión (sin LLM):
- "¿qué tareas tengo pendientes?" / "¿qué tareas tengo de [materia]?"
- "¿cómo voy en [materia]?" (nota y asistencia)
- "dame el detalle de mis componentes en [materia]"
- "¿en qué materia voy peor/mejor?"
- "¿qué necesito para aprobar [materia]?"
- "¿cuáles son mis próximos eventos?"
- "¿qué tengo esta semana?" (resumen combinado)

Esta versión usa reglas simples (detecta palabras clave) — es intencional,
para validar que los datos y la lógica de consulta funcionan antes de sumar
la complejidad de IA generativa encima.

### 8. Probar el asistente con IA real (LLM + tool-calling)

Necesitas una API key de Anthropic (gratis para probar, en
https://console.anthropic.com) pegada en `ANTHROPIC_API_KEY` dentro de tu `.env`.

```bash
python core/assistant.py
```

Aquí ya no hay palabras clave fijas — puedes preguntar de forma más natural
y libre (ej. "oye, ¿cuánto me falta para pasar redes?", "resúmeme cómo ando
esta semana") y el modelo decide por sí mismo qué función de consulta usar,
basándose únicamente en los datos reales que esa función devuelve (nunca
inventa notas ni fechas — así se evita el problema de "alucinación" típico
de los chatbots con IA generativa).

### 9. Interfaz web (chat visual)

```bash
python web/app.py
```

Abre **http://localhost:5000** en tu navegador. Es una interfaz de chat
simple donde puedes escribir tus preguntas y ver las respuestas del
asistente — esta es la parte que se muestra en la sustentación de tesis,
en vez de la consola de texto.

El servidor usa Waitress (apto para producción). Para desarrollo, con
recarga automática al editar código, arranca con `FLASK_DEBUG=1`
(PowerShell: `$env:FLASK_DEBUG=1; python web/app.py`).

**Cómo iniciar sesión en la web (2 pasos, sin contraseñas):**

1. *Token de Moodle.* Con tu sesión abierta en el aula virtual, presiona F12 →
   pestaña Network → marca "Preserve log". En otra pestaña abre
   `https://aulagradoa.unemi.edu.ec/admin/tool/mobile/launch.php?service=moodle_mobile_app&passport=12345&urlscheme=moodlemobile`.
   Aparecerá una fila `token=...` (marcada *canceled*, es normal). Clic
   derecho → Copy → Copy URL, y pega ese texto en el Paso 1 del login.
2. *Token del ERP.* Igual que en el paso 2 de este README: copia `"access"`
   (y opcionalmente `"refresh"`) de la petición `login.json`.

Si un token vence, la app lo avisa: genera uno nuevo y vuelve a entrar.

Para ver las tablas de la base de datos: `python ver_bd.py`.

### Modelo de datos (3FN)

12 tablas, definidas en `db/schema.sql`: `estudiante`, `materia`, `docente`,
`materia_docente`, `prerrequisito`, `matricula`, `componente_nota`, `tarea`,
`entrega`, `evento`, `horario`, `practica`, `sesion_extraccion` y `sesion_web`
(más `schema_version` para las migraciones). El diagrama entidad-relación está
en `docs/academia_der.dbml` (pégalo en https://dbdiagram.io/d).

Si abres una base creada con el esquema anterior, `init_db()` la detecta, la
recrea con el esquema normalizado y hay que volver a sincronizar (los datos
son una copia de Moodle/ERP, no se capturan a mano). Las finanzas quedaron
fuera, como indica el alcance de la propuesta. El carné no se guarda en la base de
datos: al pedirlo en el chat ("quiero mi carné") aparece un botón que llama a `/carnet`, que lo pide al ERP con el token de la
sesión y se lo entrega al navegador (módulo complementario).

Usa exactamente la misma lógica de `core/assistant.py` por debajo (mismo
LLM, mismas herramientas, mismos datos reales) — es solo una capa visual
encima de lo que ya está probado y funcionando.

## Qué sigue después de validar esto

1. Confirmar/completar el endpoint de refresh del SGA.
2. Cargar la malla curricular y prerrequisitos (funcionalidad 6 del alcance:
   "¿qué materias me faltan para graduarme?") — estos datos probablemente
   están en otra sección del SGA que aún no se ha inspeccionado con DevTools.
3. Investigar si existe un endpoint de horario de clases (no confirmado
   todavía - la tabla `horario` en la base de datos existe pero no se llena
   automáticamente aún).
4. Extraer el valor real de "nota mínima para aprobar" desde la API en vez
   de asumir 70 por defecto en `nota_necesaria()` (varía según el modelo
   evaluativo de cada materia, como se descubrió durante las pruebas).
5. Evaluar precisión, tiempo de respuesta y usabilidad del asistente con IA
   real (paso 8) frente al de reglas (paso 7) — esta comparación es
   justamente el tipo de evidencia que se documenta en el capítulo de
   evaluación de la tesis.

## Estructura del proyecto

```
unemi-assistant/
├── .env.example        # Plantilla de configuración (copiar a .env)
├── sga/client.py        # Conector API del SGA (notas, asistencia)
├── moodle/client.py     # Conector API de Moodle (cursos, tareas, eventos, calificaciones)
├── web/panel.py         # Datos reales del tablero (conexiones, materias, bitácora)
├── db/schema.sql        # Modelo de datos relacional
├── db/setup.py          # Inicializa la base de datos SQLite
├── core/sync_all.py     # Sincroniza Moodle + SGA (uso real)
├── core/privacidad.py   # Depura tokens/datos personales de logs y errores
├── core/query_engine.py # Motor de consultas v1: reglas simples (sin IA)
├── core/assistant.py    # Asistente v2: LLM real con tool-calling (RAG)
├── web/app.py            # Servidor Flask (interfaz web)
├── web/sessions_store.py # Sesiones web persistentes y cifradas (SQLite)
├── web/templates/        # login.html (2 pasos por token) e index.html
├── ver_bd.py             # Muestra las tablas de la base de datos
├── herramientas/diagnostico/  # Scripts de diagnóstico (salida sin datos personales)
├── tests/                # Pruebas automáticas (pytest; solo datos simulados)
└── requirements.txt
```


## Espacio de estudio (`/estudio`)

Practica por materia, videos y un tutor de conceptos. Código en `core/estudio.py`; rutas `/api/estudio/*` en `web/app.py`.

- **Práctica:** el modelo genera preguntas nuevas de opción múltiple (nunca copiadas de tus tareas o exámenes; se le pasan los nombres de tus evaluaciones activas para que las evite). La respuesta correcta se queda en el servidor hasta que el estudiante responde, y las opciones se mezclan. Solo se guarda el resultado en la tabla `practica` (cuántas acertó), no las preguntas ni las respuestas.
- **Videos:** se recomiendan como búsquedas de YouTube con enfoque educativo, no como enlaces inventados. Se descartan las búsquedas que piden exámenes resueltos, respuestas o trucos.
- **Tutor:** explica conceptos con ejemplos propios. No da respuestas de tests, tareas ni exámenes. Tiene dos capas: un filtro en código (determinista, no llama al modelo cuando detecta el pedido) y reglas en el prompt del modelo.
- Modelo configurable con `ESTUDIO_MODEL` en `.env` (por defecto `claude-sonnet-4-6`).


## Despliegue, protección y escalabilidad

- Guía paso a paso (Cloudflare Tunnel para pruebas, Render para dejarla siempre activa): `docs/DESPLIEGUE.md`.
- Protecciones: límites de uso por ventana, tope diario de consultas a la IA, cupos con sala de espera para lo lento, cabeceras de seguridad (`web/limites.py`, `web/app.py`).
- Base de datos SQLite en modo WAL con una conexión por hilo del servidor (no por sesión).
- Pruebas de carga reproducibles: `pruebas/` (ver `pruebas/README.md`). Línea base y método de medición para el capítulo de resultados.
- Limitación conocida: corre como un solo proceso; para varias réplicas hay que pasar el estado de conversación a Redis y la base a PostgreSQL.


## Interfaz (rediseño)

Después de entrar, la aplicación se organiza con un menú lateral (en el celular, barra inferior):
**Inicio** (cuatro datos grandes y lo próximo), **Materias**, **Agenda**, **Estudio** y **Mis datos**.
El asistente es un botón flotante ("Pregunta") disponible en todas las secciones.

**Mis datos** muestra qué guarda AcademIA del estudiante (solo cantidades), permite
descargarlo (`/api/mis-datos/exportar`, JSON sin contraseñas ni tokens) y borrarlo
(`/api/mis-datos/borrar`: elimina al estudiante y, en cascada, sus matrículas, notas,
entregas, eventos, prácticas, lecturas y sesiones; el catálogo de materias y docentes se conserva).
La lógica está en `web/mis_datos.py`.


## Pruebas automáticas

```bash
pip install -r requirements-dev.txt
python -m pytest
```

Usan solo datos simulados: no necesitan tokens, cuentas ni red (la red está bloqueada
durante las pruebas y el `.env` no se lee). GitHub Actions las ejecuta en cada subida
y en cada pull request (`.github/workflows/pruebas.yml`).

## Scripts de diagnóstico

Están en `herramientas/diagnostico/` y se ejecutan desde la carpeta del proyecto, por
ejemplo `python herramientas/diagnostico/debug_notas_moodle.py`.

- Usan tu **token** (nunca tu contraseña) y no lo imprimen.
- Por defecto muestran solo la *forma* de los datos o valores con nombres sustituidos
  (`Curso 1`, `Item 2`). Todo texto pasa por `core/privacidad.depurar`, que quita tokens,
  correos y números largos (cédulas).
- Los volcados crudos se guardan en `diagnostico_local/` (ignorada por Git). No los
  compartas: contienen tus datos.

## Verificación del ERP (pesos de las notas)

`python herramientas/diagnostico/verificar_erp.py` revisa si el ERP trae los pesos o máximos de cada componente de nota. Imprime solo nombres de campos y números de peso; nunca nombres, cédulas ni el token.

## Estado de entrega de las tareas

Moodle informa (o no) si una tarea ya se entregó. AcademIA nunca lo supone:

| Estado | Significado |
|---|---|
| Pendiente | Moodle confirmó que no la has entregado (o la guardaste como borrador) |
| Vencida sin entregar | Igual, pero la fecha ya pasó |
| Entregada | Moodle confirmó la entrega |
| **Sin verificar** | Moodle no informó el estado (o no se pudo consultar). No cuenta como pendiente ni como entregada |

Un estado ya verificado no se pierde si una consulta posterior falla. Las incidencias quedan
en la bitácora de lecturas (sin tokens). El límite de consultas por sincronización se ajusta
con `MAX_CONSULTAS_ENTREGA` (60 por defecto).

**Pendiente de verificar con una cuenta real** (ver `docs/VERIFICACION_MOODLE.md`): ejecuta
`python herramientas/diagnostico/verificar_moodle.py` y revisa la salida antes de compartirla.
Las fechas se manejan en `America/Guayaquil` (`core/tiempo.py`; en Windows hace falta el
paquete `tzdata`, ya incluido en `requirements.txt`).

## Novedades (detector de cambios)

Moodle y el ERP ya muestran tareas y notas, pero no avisan de que algo **cambió**. AcademIA
guarda una "foto" de tus datos tras cada lectura exitosa y la compara con la anterior
(`core/cambios.py`). La pestaña **Novedades** muestra lo que cambió desde tu última visita:
tareas nuevas, fechas movidas, notas publicadas o modificadas, eventos nuevos y cambios de
nota final o asistencia en el ERP.

Reglas (todas cubiertas por pruebas):
- Solo se compara entre lecturas **exitosas**. Si una lectura falla, no se guarda nada a medias
  y no se interpreta como un cambio; el cambio real se informa en la siguiente lectura buena.
- La primera lectura solo guarda el punto de partida (no hay con qué comparar).
- Sin duplicados: cada cambio se registra una sola vez.
- Lo que desaparece no se reporta (los eventos próximos se van solos con el tiempo), y pasar
  de "sin verificar" a un estado conocido tampoco es un cambio: es verificar.
- Se informa el valor de antes y el de después; no se interpreta ni se predice nada.
- Las novedades se ven en **Mis datos**, se incluyen en la descarga y se borran con "Borrar mis datos".

## Tu seguimiento (indicador por reglas)

En **Inicio**, la tarjeta «Tu seguimiento» junta en un solo lugar lo que pide atención hoy
(`core/seguimiento.py`). Son 6 reglas simples sobre datos ya verificados, y cada una explica
su criterio en «¿Por qué?»:

| Regla | Qué mide |
|---|---|
| Tareas vencidas sin entregar | Moodle informa que no está entregada (o en borrador) y su fecha pasó |
| Vencen en 48 horas | Lo mismo, pero con fecha dentro de las próximas 48 horas |
| Tareas sin verificar | Moodle no informó el estado: no se cuentan como pendientes ni como entregadas |
| Asistencia | La más baja entre tus materias en curso (dato del ERP, sin veredicto) |
| Novedades | Cambios detectados que aún no viste |
| Datos al día | La última lectura falló, no existe, o tiene más de 2 días |

Lo que NO hace, a propósito: no predice ni habla de "riesgo de reprobar"; no juzga notas (Moodle y el
ERP no entregan pesos ni máximos); no compara con reglamentos; no inventa un mínimo de asistencia.
Si faltan datos, dice "sin datos".



## Plan de estudio y alertas (Fase 3)

Vista **Plan** (`#plan`) y tarjeta **Alertas** en Inicio. Todo se calcula con reglas visibles; no hay predicciones.

- **Datos que pones tú**: minutos disponibles por día (no se supone ninguno: sin ellos no se arma el plan), minutos por tarea y por evaluación (60 y 120 de partida), dificultad por materia (fácil ×0,75, media ×1, difícil ×1,5) y la anticipación de las alertas (12, 24, 48 o 72 h).
- **Qué entra al plan**: solo tareas que Moodle confirma sin entregar (más las vencidas sin entregar) y eventos del calendario cuyo nombre parece una evaluación. Las tareas «sin verificar» se listan aparte.
- **Cómo se ordena**: primero lo vencido, luego por fecha de entrega más cercana; nunca se agenda después de la fecha límite. Si no cabe, se dice cuántos minutos faltan.
- **Ajustable**: cada bloque tiene «¿Por qué?» y «Quitar» (se puede volver a incluir).
- **Alertas** (sin duplicados): vence pronto, vencida sin entregar, lectura fallida y asistencia bajo un mínimo que tú fijas. Se pueden desactivar por tipo y descartar; una alerta descartada no reaparece.
- Rutas: `GET /api/plan`, `POST /api/plan/preferencias|excluir|incluir`, `GET /api/alertas`, `POST /api/alertas/vistas`.
- Estos datos aparecen en **Mis datos**, se exportan y se borran con tu cuenta.
