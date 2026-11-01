#!/bin/sh
# docker compose exec facturen wachtwoord-herstellen
# Draait als dezelfde gebruiker als de app: schrijft root in de database, dan
# kan de app een bestand dat sqlite erbij maakt daarna niet meer openen.
set -eu
if [ "$(id -u)" = "0" ]; then
    exec su-exec "${PUID}:${PGID}" python3 /opt/app/wachtwoord_herstellen.py
fi
exec python3 /opt/app/wachtwoord_herstellen.py
