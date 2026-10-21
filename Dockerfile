# AcademIA - imagen para produccion (un solo proceso, ver docs/DESPLIEGUE.md)
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    HOST=0.0.0.0 PORT=5000 DB_PATH=/data/unemi_assistant.db

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Usuario sin privilegios; /data es el volumen persistente (base de datos)
RUN useradd --create-home app && mkdir -p /data && chown -R app /app /data
USER app
VOLUME /data
EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request,os;urllib.request.urlopen('http://127.0.0.1:%s/salud'%os.getenv('PORT','5000'),timeout=4)"

CMD ["python", "web/app.py"]
