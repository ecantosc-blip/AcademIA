# AcademIA y las normas ISO

> **Alcance honesto.** AcademIA es un proyecto académico (prototipo funcional). Este documento muestra
> **cómo el diseño y la construcción se alinean con normas ISO/IEC como guía**, citando la evidencia
> concreta que existe en el repositorio. **No es una certificación** ni afirma cumplimiento formal de
> ninguna norma. Donde algo no está hecho, se dice (sección 8).

Metodología de trabajo: **Scrum** (ver sección 7).

---

## 1. ISO/IEC 25010: calidad del producto de software

| Característica | Qué se hizo | Evidencia en el proyecto |
|---|---|---|
| **Adecuación funcional** | Chatbot con datos reales de Moodle/ERP, plan de estudio con horas calculadas, recordatorios, espacio de estudio. | `core/assistant.py`, `core/planificador.py`, `core/estudio.py`; 462 pruebas automáticas. |
| **Eficiencia de desempeño** | Cupos de concurrencia (IA 16, sincronización 8), servidor Waitress con hilos configurables, respuestas de API sin caché de datos personales. | `web/limites.py`, `web/app.py` (`serve`), variables `CUPO_IA`, `CUPO_SYNC`, `THREADS`. Sin prueba de carga todavía (sección 8). |
| **Compatibilidad / interoperabilidad** | Integración con dos sistemas externos (Moodle Web Services y ERP/SGA) más Telegram y Claude, cada uno aislado en su cliente. | `moodle/client.py`, `sga/client.py`, `core/telegram.py`. |
| **Usabilidad** | Interfaz con tema claro/oscuro, barra lateral plegable, diseño adaptable a móvil, etiquetas ARIA, mensajes de error claros, inicio con lo importante primero (alertas resumidas, seguimiento plegable). | `web/templates/index.html`, `web/templates/login.html`. |
| **Fiabilidad** | Una sincronización fallida a medias hace `rollback` y conserva los últimos datos verificados; los errores no tumban la app; reintentos del bot cada 30 s. | `core/sync_all.py`, `core/trabajador.py`. |
| **Seguridad** | Solo lectura sobre Moodle y ERP; contraseña usada una vez y no guardada; tokens cifrados en sesión; CSP; límites contra abuso (sección 2). | `web/sessions_store.py`, `web/app.py`, `web/limites.py`. |
| **Mantenibilidad** | Código modular por capas, docstrings en las funciones públicas, pruebas por módulo, integración continua en cada subida. | Carpetas `core/`, `web/`, `moodle/`, `sga/`, `db/`; `tests/`; `.github/workflows/pruebas.yml`. |
| **Portabilidad** | Contenedor Docker y configuración de despliegue; configuración por variables de entorno. | `Dockerfile`, `render.yaml`, `.env.example`, `docs/DESPLIEGUE.md`. |

## 2. ISO/IEC 27001 y 27002: seguridad de la información (como guía de controles)

| Control (idea de la norma) | Cómo se aplica en AcademIA |
|---|---|
| **Control de acceso** | Sesión por cookie `HttpOnly`/`SameSite`; vencimiento de sesión (`SESSION_TTL_HORAS`, 8 h por defecto); solo se accede a datos del estudiante autenticado. |
| **Mínimo privilegio** | Solo lectura: la app no puede modificar nada en Moodle ni en el ERP. |
| **Criptografía** | Datos de sesión cifrados con Fernet (`web/sessions_store.py`); los códigos de Telegram se guardan solo como hash SHA-256. |
| **Gestión de credenciales** | La contraseña de Moodle se envía una vez a `login/token.php`, se descarta y nunca se guarda ni se registra; el resto del tiempo se trabaja con un token que vence. Hay freno por usuario (5 intentos / 10 min) para que la página no sirva para adivinar contraseñas. |
| **Registro sin secretos** | `core/privacidad.py` depura URLs, tokens y contraseñas de cualquier mensaje de error o de registro; hay pruebas que lo verifican. |
| **Protección contra abuso** | Límites por IP, por usuario y por sesión; presupuesto diario de la IA; cabeceras de seguridad y CSP. |
| **Desarrollo seguro** | Pruebas automáticas de privacidad (por ejemplo, que los scripts de diagnóstico no impriman tokens), integración continua, secretos solo en `.env` (fuera del repositorio). |
| **Respeto a controles del proveedor** | El login del ERP tiene detección de bots; **no se automatiza ni se elude**. El ERP es opcional y se conecta con un token que aporta el estudiante. |

## 3. Privacidad y datos (ISO/IEC 29100 y 27701 como referencia)

- **Minimización:** solo se guardan los datos académicos necesarios; la foto de perfil no se almacena (se descarga en cada sesión); el chat de Telegram solo guarda un identificador de chat.
- **Transparencia:** la pantalla de ingreso explica qué puede leer, qué no puede y qué se guarda.
- **Derechos de la persona:** la sección **Mis datos** permite ver, exportar (JSON) y borrar todo lo que la app guarda (`web/mis_datos.py`).
- **Consentimiento:** los recordatorios por Telegram son opcionales (*opt-in*), se vinculan con un código de un solo uso de 10 minutos y se cancelan con `/stop` o desde la app.
- **Aviso:** los mensajes de bots de Telegram no están cifrados de extremo a extremo; la interfaz lo indica.

## 4. ISO/IEC 25012: calidad de los datos

- **Exactitud y trazabilidad:** cada respuesta del chatbot muestra de dónde salió el dato y cuándo se sincronizó.
- **Completitud honesta:** lo que no se pudo confirmar aparece como **«Sin verificar»**; no se afirma que una tarea esté o no entregada si Moodle no lo informa.
- **Actualidad:** la última sincronización se muestra al estudiante; los recordatorios usan los últimos datos sincronizados.
- **Sin invención:** los datos del chatbot salen del motor de consultas (`core/query_engine.py`) y de herramientas del plan; las horas del plan las calcula el código, no el modelo de lenguaje.

## 5. ISO/IEC/IEEE 29148: requisitos (resumen)

**Requisitos funcionales**
- RF1. Autenticarse con la cuenta de Moodle (opcionalmente conectar el ERP).
- RF2. Sincronizar materias, notas, tareas y (con ERP) horario y registro académico.
- RF3. Consultar al chatbot sobre datos académicos propios.
- RF4. Generar un plan de estudio semanal a partir de horas libres, clases y actividades.
- RF5. Editar el horario: agregar rutina, mover bloques de estudio (queda fijado), borrar.
- RF6. Recibir alertas y novedades; recibir recordatorios opcionales por Telegram.
- RF7. Practicar y recibir ayuda de estudio sin resolver evaluaciones activas.
- RF8. Ver, exportar y borrar los propios datos.

**Requisitos no funcionales**
- RNF1. Solo lectura sobre los sistemas institucionales.
- RNF2. No almacenar contraseñas.
- RNF3. Cifrar la sesión y vencerla.
- RNF4. Responder bajo carga acotada mediante límites y cupos.
- RNF5. Funcionar en computador y en celular, con tema claro y oscuro.
- RNF6. Mantener cobertura de pruebas automáticas en cada cambio.

## 6. ISO/IEC 9241-11 y 9241-210: usabilidad y diseño centrado en la persona

- **Eficacia y eficiencia:** el ingreso pasa de copiar dos tokens a usuario y contraseña; lo importante del día se ve en la pantalla de inicio.
- **Satisfacción y accesibilidad:** contraste con tema oscuro, etiquetas ARIA, navegación por teclado en el horario, mensajes sin tecnicismos.
- **Iteración:** las pantallas se ajustaron con la retroalimentación de la docente tutora y del equipo (por ejemplo, el horario con pestañas en lugar de una pantalla sobrecargada). **[Completar con pruebas de usabilidad con estudiantes cuando se hagan.]**

## 7. ISO/IEC/IEEE 12207 (ciclo de vida) y Scrum

AcademIA se desarrolla con **Scrum**: entregas incrementales, retroalimentación frecuente y pruebas en cada incremento.

| Elemento de Scrum | En este proyecto |
|---|---|
| Roles | *Product Owner*: **[completar: docente tutora u otro]**. *Scrum Master* y *equipo de desarrollo*: **[completar]**. Autores: Erick Cantos y Josué Guadalupe. |
| Product Backlog | Los requisitos RF1–RF8 y RNF1–RNF6 de la sección 5. |
| Incrementos (ejemplos ya entregados) | 1) Base de datos y sincronización con Moodle/ERP. 2) Chatbot con datos reales. 3) Seguridad, límites y privacidad. 4) Interfaz con identidad UNEMI. 5) Plan conversacional con horas. 6) Recordatorios por Telegram. 7) Horario editable y preguntas con botones. 8) Ingreso con credencial de Moodle. |
| Fechas de *sprint*, revisiones y retrospectivas | **[completar con las fechas reales del equipo; este documento no las inventa]** |
| Definición de «terminado» | Pruebas automáticas en verde, sin secretos en registros, documentación actualizada y revisión visual de la pantalla afectada. |

Correspondencia con 12207 (procesos): requisitos (sección 5), diseño (`docs/AcademIA_ER.png`, `docs/academia_der.dbml`), implementación (código), verificación (pruebas + integración continua), validación (pendiente: pruebas con estudiantes reales) y mantenimiento (documentación y despliegue).

## 8. ISO/IEC/IEEE 29119: pruebas

- **Pruebas automáticas:** 462 casos con `pytest` (unidad e integración con servicios simulados) que se ejecutan en cada subida mediante GitHub Actions.
- **Qué cubren:** planificador, notas de Moodle, sincronización, cambios detectados, límites, privacidad, sesión, Telegram, edición de horario, login por credenciales y exportación/borrado de datos.
- **Pruebas manuales/visuales:** capturas de las pantallas con un navegador automatizado.

### Límites conocidos (transparencia)

- No se ha hecho una **prueba de carga** medida; la capacidad indicada sale de la configuración.
- El chatbot y Telegram se probaron con **simulaciones**; falta una validación extremo a extremo con cuentas reales.
- El **ERP no se automatiza** por su detección de bots; sin ERP no hay horario de clases ni foto del ERP.
- Los recordatorios solo salen **mientras la app esté corriendo**.
- Los porcentajes de notas de Moodle no se interpretan (solo el orden entre materias).
- Pedir usuario y contraseña en un sitio que **no es oficial de la UNEMI** exige confianza: lo recomendable para un despliegue real es que **TI de la UNEMI** lo autorice o provea inicio de sesión institucional (OAuth/SSO), que quedaría como trabajo futuro.
