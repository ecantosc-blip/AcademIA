"""
Genera en el momento el PDF del carne, a partir de los datos del "carne digital" del ERP.

Por que existe: desde noviembre de 2026 el ERP dejo de entregar un PDF/PNG ya hecho
(eCarnet.pdf y eCarnet.png_anverso vienen vacios); en su lugar entrega los datos sueltos
en eCarnetDigital (nombre, cedula, carrera, periodo, codigo QR...) para que su propia
pagina los dibuje. Como AcademIA necesita seguir ofreciendo una descarga en PDF, este modulo
arma ese PDF con esos mismos datos (ver sga.client.SGAClient.get_carnet_parseado).

No se inventa ningun dato: todo sale de lo que el ERP ya certifico para esta persona, incluido
el mismo codigo QR de validacion que usa el ERP (asi que se puede verificar igual). Se deja
explicito en el propio PDF que es una copia hecha por AcademIA y no el documento oficial del
ERP, con el enlace de validacion para comprobarlo.
"""
import base64
import io
import textwrap

from reportlab.lib.pagesizes import landscape
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

AZUL = (0 / 255, 46 / 255, 69 / 255)      # #002E45, el azul institucional de la UNEMI
NARANJA = (255 / 255, 105 / 255, 0 / 255)  # #FF6900
ANCHO, ALTO = 148 * mm, 85 * mm           # un poco mas grande que una tarjeta, para que quepa todo


def _qr_a_imagen(qr_data_uri: str):
    """El ERP entrega el QR como 'data:image/png;base64,...': se decodifica, sin red."""
    if not qr_data_uri or "," not in qr_data_uri:
        return None
    try:
        return ImageReader(io.BytesIO(base64.b64decode(qr_data_uri.split(",", 1)[1])))
    except Exception:
        return None


def _foto_a_imagen(foto_bytes):
    if not foto_bytes:
        return None
    try:
        return ImageReader(io.BytesIO(foto_bytes))
    except Exception:
        return None


def generar_pdf(datos: dict, foto_bytes: bytes = None) -> bytes:
    """`datos` es lo que devuelve SGAClient.get_carnet_parseado(). Devuelve los bytes del PDF."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=landscape((ANCHO, ALTO)))
    w, h = landscape((ANCHO, ALTO))

    c.setFillColorRGB(*AZUL)
    c.rect(0, 0, w, h, fill=1, stroke=0)
    c.setFillColorRGB(*NARANJA)
    c.rect(0, h - 3, w, 3, fill=1, stroke=0)   # franja superior, como en el tema de la app

    c.setFillColorRGB(1, 1, 1)
    c.setFont("Helvetica-Bold", 12)
    c.drawString(8 * mm, h - 11 * mm, "UNIVERSIDAD ESTATAL DE MILAGRO")
    c.setFont("Helvetica", 8.5)
    c.drawString(8 * mm, h - 16 * mm, "Carné estudiantil digital")

    qs = 22 * mm   # el QR se reserva primero: el resto del ancho es para texto y foto
    qr = _qr_a_imagen(datos.get("qr_data_uri"))
    if qr:
        c.drawImage(qr, w - qs - 7 * mm, 7 * mm, width=qs, height=qs)

    foto = _foto_a_imagen(foto_bytes)
    fx, fy, fs = 8 * mm, 24 * mm, 32 * mm
    if foto:
        c.drawImage(foto, fx, fy, width=fs, height=fs, preserveAspectRatio=True,
                   anchor='c', mask='auto')
    else:
        c.setFillColorRGB(1, 1, 1)
        c.setLineWidth(0.5)
        c.rect(fx, fy, fs, fs, fill=0, stroke=1)

    nombre = f"{(datos.get('nombres') or '').strip()} {(datos.get('apellidos') or '').strip()}".strip()
    tx, tx_max = fx + fs + 6 * mm, w - qs - 12 * mm   # el texto no invade la columna del QR
    ancho_texto = tx_max - tx
    c.setFillColorRGB(1, 1, 1)
    c.setFont("Helvetica-Bold", 11)
    for i, linea in enumerate(textwrap.wrap(nombre or "(sin nombre)", 28)[:2]):
        c.drawString(tx, h - 25 * mm - i * 4.6 * mm, linea)

    lineas = [
        ("Cédula", datos.get("cedula")),
        ("Carrera", datos.get("carrera")),
        ("Facultad", datos.get("facultad_siglas") or datos.get("facultad")),
        ("Modalidad", datos.get("modalidad")),
        ("Nivel", datos.get("nivel")),
        ("Período", datos.get("periodo")),
        ("Código", datos.get("codigo")),
    ]
    c.setFont("Helvetica", 8)
    y = h - 36 * mm
    etiqueta_ancho = 20 * mm
    for etiqueta, valor in lineas:
        if not valor:
            continue
        c.setFillColorRGB(0.75, 0.82, 0.87)
        c.drawString(tx, y, f"{etiqueta}:")
        c.setFillColorRGB(1, 1, 1)
        max_chars = max(10, int((ancho_texto - etiqueta_ancho) / (1.5 * mm)))
        c.drawString(tx + etiqueta_ancho, y, str(valor)[:max_chars])
        y -= 4.6 * mm

    c.setFillColorRGB(0.75, 0.82, 0.87)
    c.setFont("Helvetica", 5.4)
    pie = ("Generado por AcademIA con los datos de tu carné digital en el SGA — no es el documento "
          "oficial del ERP. Valida el QR o entra a " + (datos.get("url_validacion") or "sga.unemi.edu.ec"))
    for i, linea in enumerate(textwrap.wrap(pie, 118)):
        c.drawString(8 * mm, 7 * mm - i * 2.8 * mm, linea)

    c.showPage()
    c.save()
    return buf.getvalue()
