ARG BUILD_FROM
FROM ${BUILD_FROM}

ENV LANG=C.UTF-8 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DATA_DIR=/data

WORKDIR /opt/app

COPY app/requirements.txt /opt/app/requirements.txt

# freetype/jpeg/zlib zijn runtime-libs voor reportlab; de build-deps worden na
# het installeren van de wheels weer verwijderd zodat het image klein blijft.
RUN \
    apk add --no-cache \
        freetype \
        jpeg \
        zlib \
    && apk add --no-cache --virtual .build-deps \
        build-base \
        freetype-dev \
        jpeg-dev \
        zlib-dev \
    && pip3 install --no-cache-dir --break-system-packages -r requirements.txt \
    && apk del --no-network .build-deps

COPY app /opt/app
COPY rootfs /

RUN chmod a+x /usr/bin/facturen-app

ARG BUILD_ARCH
ARG BUILD_VERSION
LABEL \
    io.hass.name="Facturen App" \
    io.hass.description="Simpele facturatie add-on voor Home Assistant" \
    io.hass.type="addon" \
    io.hass.arch="${BUILD_ARCH}" \
    io.hass.version="${BUILD_VERSION}"

CMD ["/usr/bin/facturen-app"]
