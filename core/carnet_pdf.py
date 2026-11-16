"""
Genera en el momento el PDF del carne, a partir de los datos del "carne digital" del ERP.

Por que existe: desde noviembre de 2026 el ERP dejo de entregar un PDF/PNG ya hecho
(eCarnet.pdf y eCarnet.png_anverso vienen vacios); en su lugar entrega los datos sueltos
en eCarnetDigital (nombre, cedula, carrera, periodo, codigo QR...) para que su propia
pagina los dibuje, como una tarjeta vertical con foto circular. Como AcademIA necesita
seguir ofreciendo una descarga en PDF, este modulo arma una tarjeta parecida con esos
mismos datos (ver sga.client.SGAClient.get_carnet_parseado).

No se inventa ningun dato: todo sale de lo que el ERP ya certifico para esta persona,
incluido el mismo codigo QR de validacion que usa el ERP (asi que se puede verificar
igual). Se deja explicito en el propio PDF que es una copia hecha por AcademIA y no el
documento oficial del ERP, con el enlace de validacion para comprobarlo.
"""
import base64
import io
import textwrap

from reportlab.lib.pagesizes import mm as _mm  # noqa: F401  (solo para dejar claro que todo usa mm)
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

AZUL = (0 / 255, 46 / 255, 69 / 255)        # #002E45, el azul institucional de la UNEMI
NARANJA = (255 / 255, 105 / 255, 0 / 255)   # #FF6900
GRIS_ETQ = (0.52, 0.57, 0.6)
GRIS_LINEA = (0.86, 0.88, 0.9)
BLANCO = (1, 1, 1)
ANCHO, ALTO = 72 * mm, 148 * mm             # tarjeta vertical, como la del SGA (con espacio de sobra)


def _imagen(datos_bytes):
    if not datos_bytes:
        return None
    try:
        return ImageReader(io.BytesIO(datos_bytes))
    except Exception:
        return None


def _qr_a_imagen(qr_data_uri: str):
    """El ERP entrega el QR como 'data:image/png;base64,...': se decodifica, sin red."""
    if not qr_data_uri or "," not in qr_data_uri:
        return None
    try:
        return _imagen(base64.b64decode(qr_data_uri.split(",", 1)[1]))
    except Exception:
        return None


def _texto_centrado(c, cx, y, texto, fuente, tam, color=BLANCO):
    c.setFillColorRGB(*color)
    c.setFont(fuente, tam)
    c.drawCentredString(cx, y, texto)


def _foto_circular(c, img, cx, cy, r):
    """Recorta la imagen a un circulo (clipping path) para que se vea como la del SGA."""
    c.saveState()
    recorte = c.beginPath()
    recorte.circle(cx, cy, r)
    c.clipPath(recorte, stroke=0, fill=0)
    if img:
        c.drawImage(img, cx - r, cy - r, width=2 * r, height=2 * r,
                   preserveAspectRatio=True, anchor='c', mask='auto')
    else:
        c.setFillColorRGB(0.88, 0.91, 0.93)
        c.rect(cx - r, cy - r, 2 * r, 2 * r, fill=1, stroke=0)
    c.restoreState()
    c.setStrokeColorRGB(*NARANJA)
    c.setLineWidth(1.3)
    c.circle(cx, cy, r, fill=0, stroke=1)


def generar_pdf(datos: dict, foto_bytes: bytes = None) -> bytes:
    """`datos` es lo que devuelve SGAClient.get_carnet_parseado(). Devuelve los bytes del PDF."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(ANCHO, ALTO))
    w, h = ANCHO, ALTO
    cx = w / 2

    # Tarjeta: fondo blanco con el borde y la cabecera en los colores de la UNEMI.
    c.setFillColorRGB(1, 1, 1)
    c.rect(0, 0, w, h, fill=1, stroke=0)
    c.setStrokeColorRGB(*GRIS_LINEA)
    c.setLineWidth(1)
    c.rect(0.5, 0.5, w - 1, h - 1, fill=0, stroke=1)

    cab_h = 16 * mm
    c.setFillColorRGB(*AZUL)
    c.rect(0, h - cab_h, w, cab_h, fill=1, stroke=0)
    c.setFillColorRGB(*NARANJA)
    c.rect(0, h - 1.4 * mm, w, 1.4 * mm, fill=1, stroke=0)
    _texto_centrado(c, cx, h - 7 * mm, "UNIVERSIDAD ESTATAL DE MILAGRO", "Helvetica-Bold", 7)
    _texto_centrado(c, cx, h - 12 * mm, "Carné estudiantil digital", "Helvetica", 6.5)

    # Foto circular, igual que en el SGA.
    r_foto = 13 * mm
    cy_foto = h - cab_h - r_foto - 4 * mm
    _foto_circular(c, _imagen(foto_bytes), cx, cy_foto, r_foto)

    nombre = f"{(datos.get('nombres') or '').strip()} {(datos.get('apellidos') or '').strip()}".strip()
    y = cy_foto - r_foto - 6 * mm
    c.setFillColorRGB(*AZUL)
    c.setFont("Helvetica-Bold", 10)
    lineas_nombre = textwrap.wrap(nombre or "(sin nombre)", 22)[:2]
    for i, linea in enumerate(lineas_nombre):
        c.drawCentredString(cx, y - i * 4.2 * mm, linea)
    y -= len(lineas_nombre) * 4.2 * mm + 2 * mm

    # Etiqueta "ESTUDIANTE UNEMI", como el distintivo del carné del SGA.
    etq = "ESTUDIANTE UNEMI"
    c.setFont("Helvetica-Bold", 6)
    etq_w = c.stringWidth(etq, "Helvetica-Bold", 6) + 8 * mm
    c.setFillColorRGB(*AZUL)
    c.roundRect(cx - etq_w / 2, y - 4.6 * mm, etq_w, 4.6 * mm, 2.3 * mm, fill=1, stroke=0)
    _texto_centrado(c, cx, y - 3.4 * mm, etq, "Helvetica-Bold", 6)
    y -= 8 * mm

    # Ficha de datos, en dos columnas como la tabla "Estado del carné" del SGA.
    campos = [
        ("CÉDULA", datos.get("cedula")),
        ("CARRERA", datos.get("carrera")),
        ("FACULTAD", datos.get("facultad_siglas") or datos.get("facultad")),
        ("MODALIDAD", datos.get("modalidad")),
    ]
    limite_y = 24 * mm + 9 * mm + 4 * mm  # tope del pie (pie_h + disclaimer_h) + margen, para no pisarlo
    c.setLineWidth(0.4)
    for etiqueta, valor in campos:
        if not valor or y - 9.8 * mm < limite_y:
            continue
        c.setStrokeColorRGB(*GRIS_LINEA)
        c.line(5 * mm, y, w - 5 * mm, y)
        y -= 3.6 * mm
        c.setFillColorRGB(*GRIS_ETQ)
        c.setFont("Helvetica-Bold", 5.6)
        c.drawString(5 * mm, y, etiqueta)
        y -= 3.6 * mm
        c.setFillColorRGB(*AZUL)
        c.setFont("Helvetica-Bold", 7.2)
        max_chars = max(10, int((w - 10 * mm) / (1.6 * mm)))
        c.drawString(5 * mm, y, str(valor)[:max_chars])
        y -= 2.6 * mm

    # Pie: nivel/periodo a la izquierda, QR a la derecha (igual que el carne del SGA).
    # Se deja flotando sobre una franja blanca inferior (disclaimer) que nunca se pisan.
    disclaimer_h = 9 * mm
    pie_h = 24 * mm
    pie_y0 = disclaimer_h
    c.setFillColorRGB(*AZUL)
    c.rect(0, pie_y0, w, pie_h, fill=1, stroke=0)

    qs = 16 * mm
    qr = _qr_a_imagen(datos.get("qr_data_uri"))
    if qr:
        c.setFillColorRGB(1, 1, 1)
        c.rect(w - qs - 5 * mm, pie_y0 + 4 * mm, qs + 2 * mm, qs + 2 * mm, fill=1, stroke=0)
        c.drawImage(qr, w - qs - 4 * mm, pie_y0 + 5 * mm, width=qs, height=qs)

    tx = 4 * mm
    c.setFillColorRGB(0.75, 0.82, 0.87)
    c.setFont("Helvetica", 5.4)
    c.drawString(tx, pie_y0 + pie_h - 6 * mm, "NIVEL")
    c.setFillColorRGB(1, 1, 1)
    c.setFont("Helvetica-Bold", 6.8)
    c.drawString(tx, pie_y0 + pie_h - 10 * mm, str(datos.get("nivel") or "—")[:16])
    if datos.get("periodo"):
        c.setFillColorRGB(0.75, 0.82, 0.87)
        c.setFont("Helvetica", 5.4)
        c.drawString(tx, pie_y0 + pie_h - 15 * mm, "PERÍODO")
        c.setFillColorRGB(1, 1, 1)
        c.setFont("Helvetica-Bold", 5.6)
        c.drawString(tx, pie_y0 + pie_h - 19 * mm, str(datos["periodo"])[:26])

    # Franja blanca inferior: de donde salio esto y como validarlo (no es el PDF oficial).
    c.setFillColorRGB(1, 1, 1)
    c.rect(0, 0, w, disclaimer_h, fill=1, stroke=0)
    c.setFillColorRGB(*GRIS_ETQ)
    c.setFont("Helvetica", 4.4)
    pie_txt = ("Generado por AcademIA con tu carné digital del SGA — no es el documento oficial. "
              "Valida el QR o entra a " + (datos.get("url_validacion") or "sga.unemi.edu.ec"))
    lineas_pie = textwrap.wrap(pie_txt, 50)
    y0 = disclaimer_h - 3 * mm
    for i, linea in enumerate(lineas_pie):
        c.drawCentredString(cx, y0 - i * 2.6 * mm, linea)

    c.showPage()
    c.save()
    return buf.getvalue()
