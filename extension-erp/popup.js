const $ = (id) => document.getElementById(id);

function formatoEdad(ms) {
  const min = Math.round(ms / 60000);
  if (min < 1) return "hace un momento";
  if (min < 60) return `hace ${min} min`;
  return `hace ${Math.round(min / 60)} h`;
}

chrome.storage.local.get(["sga_access_token", "sga_capturado_en"], (d) => {
  const estado = $("estado");
  if (!d.sga_access_token) {
    estado.className = "estado falta";
    estado.textContent =
      "Todavía no he visto tu token. Abre el ERP, inicia sesión y navega un momento por él.";
    return;
  }
  const edadMs = Date.now() - (d.sga_capturado_en || 0);
  const viejo = edadMs > 90 * 60 * 1000; // el ERP suele vencer ~2h: a partir de 90 min, aviso
  estado.className = "estado ok";
  estado.textContent =
    `Token detectado, capturado ${formatoEdad(edadMs)}.` +
    (viejo ? " Puede estar por vencer: si falla, vuelve a abrir el ERP un momento." : "");
  $("rellenar").disabled = false;
  $("copiar").disabled = false;
});

$("copiar").addEventListener("click", async () => {
  const { sga_access_token: token } = await chrome.storage.local.get("sga_access_token");
  if (!token) return;
  await navigator.clipboard.writeText(token);
  $("copiar").textContent = "¡Copiado!";
  setTimeout(() => ($("copiar").textContent = "Copiar token"), 1500);
});

$("rellenar").addEventListener("click", async () => {
  const { sga_access_token: token } = await chrome.storage.local.get("sga_access_token");
  if (!token) return;
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab || !tab.id) return;

  try {
    const [{ result }] = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: (valor) => {
        const campo = document.getElementById("sga_access_token");
        if (!campo) return "sin_campo";
        const detalle = campo.closest("details");
        if (detalle) detalle.open = true; // el campo del ERP esta plegado por ser opcional
        campo.value = valor;
        campo.dispatchEvent(new Event("input", { bubbles: true })); // dispara la validacion del login
        campo.scrollIntoView({ block: "center", behavior: "smooth" });
        return "ok";
      },
      args: [token],
    });
    const estado = $("estado");
    if (result === "sin_campo") {
      estado.className = "estado falta";
      estado.textContent = 'No encontré el campo del ERP en esta pestaña. Abre la página de login de AcademIA primero.';
    } else {
      estado.className = "estado ok";
      estado.textContent = "Listo: el token quedó puesto en el formulario.";
    }
  } catch (e) {
    $("estado").className = "estado falta";
    $("estado").textContent = "No pude escribir en esta pestaña (" + e.message + ").";
  }
});
