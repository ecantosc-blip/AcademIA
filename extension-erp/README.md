# Extensión AcademIA – Token del ERP

Evita copiar y pegar a mano el token del ERP cada vez que entras a AcademIA.

**Qué hace.** Mientras tienes el ERP de la UNEMI abierto y ya iniciaste sesión normalmente (tú,
en tu navegador, pasando su verificación como cualquier persona), la extensión observa el token
que tu propio navegador ya envía y lo guarda. Luego, con un clic, lo escribe en el campo
"Token del ERP" del login de AcademIA.

De paso, si el propio ERP llega a renovar su sesión mientras navegas, la extensión también
intenta capturar el **token de renovación**. Esto no está garantizado en cada visita (depende
de que el ERP decida renovarse justo en ese momento): si tu cuenta tiene más de una carrera
(por ejemplo, nivelación y una carrera de grado), ese token es el que permite cambiarte
automáticamente a la carrera de grado para ver su horario y sus notas. Si no se capturó, puedes
seguir pegándolo a mano, como ya era opcional antes.

**Qué NO hace.**
- No inicia sesión por ti en el ERP ni toca su formulario de usuario/contraseña.
- No ve ni guarda tu contraseña del ERP.
- No envía el token a ningún sitio: solo queda en tu navegador, y solo se usa cuando tú pulsas
  el botón sobre la página de AcademIA.

## Instalar (modo desarrollador, mientras no esté en la Chrome Web Store)

1. Abre `chrome://extensions` en Chrome (o `edge://extensions` en Edge).
2. Activa **"Modo de desarrollador"** (arriba a la derecha).
3. Pulsa **"Cargar descomprimida"** y selecciona esta carpeta (`extension-erp`).
4. Verás el tigre de AcademIA en la barra de extensiones.

## Usar

1. Abre el ERP (`sga.unemi.edu.ec`) en una pestaña e inicia sesión como siempre.
2. Navega un momento dentro del ERP (por ejemplo, abre "Mis materias"), para que la extensión
   alcance a ver una petición con tu token.
3. Abre AcademIA, entra a la página de login y abre "Conectar también el ERP (opcional)".
4. Haz clic en el ícono de la extensión → **"Rellenar en esta pestaña"**. El campo del token
   se llena solo.
5. Completa tu usuario y contraseña de Moodle como siempre, y pulsa "Entrar".

El token del ERP vence a las pocas horas; si te marca error, repite los pasos 1–4.

## Por qué no hace más que esto

El login del ERP tiene protección contra bots (detección de automatización). Esta extensión
no la evade: el inicio de sesión siempre lo hace una persona, a mano, en su propio navegador.
Por eso tampoco puede traer el *token de renovación* automáticamente (solo aparece en la
respuesta de red del login, no en los encabezados que esta extensión observa); si lo quieres,
se sigue copiando a mano como hasta ahora, o puedes dejarlo vacío.
