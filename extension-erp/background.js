/*
 * AcademIA - Token del ERP (extension del navegador)
 * -----------------------------------------------------------------------
 * Que hace: mientras el ERP de la UNEMI (sga.unemi.edu.ec / sgaestudiante.unemi.edu.ec)
 * esta abierto y la PERSONA ya inicio sesion normalmente en su propio navegador, esta
 * extension observa (sin modificar nada) las peticiones que el navegador envia a esos
 * dos dominios y guarda el valor del encabezado "Authorization" (el token de acceso)
 * para poder rellenarlo despues en el login de AcademIA con un clic.
 *
 * Que NO hace:
 *   - No inicia sesion por la persona ni completa el formulario de usuario/contrasena
 *     del ERP: el login normal (con su deteccion de bots) lo hace siempre un humano.
 *   - No lee ni envia nada a ningun otro sitio: host_permissions solo cubre esos dos
 *     dominios del ERP, y el token nunca sale de este navegador salvo cuando la propia
 *     persona pulsa "Rellenar en esta pestana" sobre la pagina de login de AcademIA.
 *   - No guarda la contrasena del ERP: esta extension nunca la ve.
 *
 * El token capturado se guarda solo en el almacenamiento local de la extension
 * (chrome.storage.local, dentro de este navegador) y se reemplaza cada vez que se ve
 * uno nuevo. Al desinstalar la extension, Chrome borra ese almacenamiento.
 */

const DOMINIOS_ERP = ["sga.unemi.edu.ec", "sgaestudiante.unemi.edu.ec"];

function esDominioErp(url) {
  try {
    const host = new URL(url).hostname;
    return DOMINIOS_ERP.some((d) => host === d || host.endsWith("." + d));
  } catch (e) {
    return false;
  }
}

chrome.webRequest.onSendHeaders.addListener(
  (detalle) => {
    if (!esDominioErp(detalle.url)) return;
    const cab = (detalle.requestHeaders || []).find(
      (h) => h.name.toLowerCase() === "authorization"
    );
    if (!cab || !cab.value) return;
    const token = cab.value.replace(/^Bearer\s+/i, "").trim();
    if (!token || token.split(".").length !== 3) return; // solo JWT de verdad (3 partes)
    chrome.storage.local.set({
      sga_access_token: token,
      sga_capturado_en: Date.now(),
    });
  },
  { urls: DOMINIOS_ERP.map((d) => `https://${d}/*`) },
  ["requestHeaders", "extraHeaders"]
);

// El token de renovacion ("refresh") solo aparece en el CUERPO de la respuesta del login,
// que esta extension no lee (eso pediria un permiso mas invasivo). Lo que SI se puede leer sin
// pedir nada nuevo es el cuerpo de las peticiones que el propio ERP envia a SU endpoint de
// renovacion cuando decide refrescar su sesion solo: ese cuerpo lleva el refresh que el ERP
// ya tenia guardado. Es un "si cae, cae": solo se captura si el ERP llega a renovarse mientras
// la persona esta navegando con la extension activa, no esta garantizado en cada visita.
chrome.webRequest.onBeforeRequest.addListener(
  (detalle) => {
    if (!esDominioErp(detalle.url) || !detalle.url.includes("/jwt/token/refresh")) return;
    const bruto = detalle.requestBody && detalle.requestBody.raw && detalle.requestBody.raw[0];
    if (!bruto || !bruto.bytes) return;
    try {
      const texto = new TextDecoder("utf-8").decode(bruto.bytes);
      const refresh = JSON.parse(texto).refresh;
      if (refresh) chrome.storage.local.set({ sga_refresh_token: refresh, sga_refresh_capturado_en: Date.now() });
    } catch (e) {
      /* cuerpo con otro formato: se ignora, no es grave (el refresh sigue siendo opcional) */
    }
  },
  { urls: DOMINIOS_ERP.map((d) => `https://${d}/*`) },
  ["requestBody"]
);
