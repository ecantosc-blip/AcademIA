# Extensión AcademIA – Token del ERP

Evita copiar y pegar a mano el token del ERP cada vez que entras a AcademIA.

**Qué hace.** Mientras tienes el ERP de la UNEMI abierto y ya iniciaste sesión normalmente (tú,
en tu navegador, pasando su verificación como cualquier persona), la extensión observa el token
que tu propio navegador ya envía y lo guarda. Luego, con un clic, lo escribe en el campo
"Token del ERP" del login de AcademIA.

La extensión también captura el **token de renovación** (el que permite cambiarte
automáticamente a otra carrera si tu cuenta tiene más de una, por ejemplo nivelación y una
carrera de grado). Ese token solo viene en el cuerpo de la respuesta del login, así que, justo
mientras inicias sesión en el ERP, la extensión se conecta un instante al Protocolo de
Depuración de Chrome, pausa nada más esa única respuesta el tiempo justo para leerla y la deja
seguir su curso normal enseguida. **Vas a ver un aviso amarillo de Chrome que dice algo como
"AcademIA – Token del ERP está depurando este navegador".** Es normal, Chrome lo muestra
siempre que algo usa ese protocolo (no se puede ocultar, y es correcto que avise), y es
justamente para que sepas qué está pasando: es la misma información que verías a mano abriendo
F12 → Network → `login.json`, solo que automático. El aviso desaparece solo, en un par de
segundos, en cuanto la extensión termina de leerlo.

Si por algún motivo no lo logra capturar (por ejemplo, si ya tenías las herramientas de
desarrollador abiertas en esa pestaña), puedes seguir pegándolo a mano, como ya era opcional.

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
