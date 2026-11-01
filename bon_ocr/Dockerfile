# De bonnenlezer als eigen container. Alleen bereikbaar vanuit de facturen-app.
#
# Debian en niet Alpine, anders dan de app: onnxruntime wordt niet als wheel
# voor musl uitgebracht, dus op Alpine zou pip het van de bron moeten bouwen.
# Dat is een bouw van een half uur per architectuur en een veel groter image.
FROM python:3.12-slim-bookworm

ENV LANG=C.UTF-8 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8099 \
    TZ=Europe/Amsterdam \
    LOG_LEVEL=info \
    OCR_DRADEN=4

WORKDIR /opt/bon-ocr

# pdfimages en pdftoppm, om het plaatje uit een PDF-scan te halen.
# hadolint ignore=DL3008
RUN apt-get update \
    && apt-get install --no-install-recommends -y poppler-utils tzdata \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /opt/bon-ocr/requirements.txt
RUN pip3 install --no-cache-dir -r requirements.txt

COPY bon_parser.py lezer.py /opt/bon-ocr/

# De modellen nu binnenhalen in plaats van bij de eerste bon. Twee redenen:
# de Pi hoeft dan niet aan het internet te kunnen om te werken, en de eerste
# scan na een herstart staat niet dertig seconden stil op een download.
RUN python3 -c "\
import numpy as np;\
from rapidocr import RapidOCR;\
RapidOCR(params={'Global.log_level': 'error'})(np.full((80, 240, 3), 255, dtype='uint8'))"

# De app draait niet als root. Zelfde gebruiker en nummer als de facturen-app,
# zodat de logs in docker compose bij elkaar passen.
RUN useradd --uid 1000 --create-home --shell /usr/sbin/nologin bonocr \
    && chown -R bonocr:bonocr /opt/bon-ocr /home/bonocr
USER bonocr

EXPOSE 8099

HEALTHCHECK --interval=60s --timeout=5s --start-period=20s --retries=3 \
    CMD python3 -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('PORT','8099') + '/gezond', timeout=4)" || exit 1

CMD ["python3", "lezer.py"]
