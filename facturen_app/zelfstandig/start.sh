#!/bin/sh
# Start de Facturen App als losse container (zonder Home Assistant).
set -eu

# Docker maakt ./data zelf aan als die er nog niet is, en dan is root de
# eigenaar. Een gekopieerde administratie uit Home Assistant heeft ook vaak
# root als eigenaar. De app draait als PUID:PGID (standaard 1000, de eerste
# gebruiker op een Raspberry Pi), dus die moet erin kunnen schrijven. Alleen
# wat nog niet klopt wordt aangepast, zodat een herstart snel blijft.
if [ "$(id -u)" = "0" ]; then
    mkdir -p "${DATA_DIR}"
    find "${DATA_DIR}" \( ! -user "${PUID}" -o ! -group "${PGID}" \) \
        -exec chown "${PUID}:${PGID}" {} +
    echo "Facturen App wordt gestart op poort ${PORT} (tijdzone ${TZ})..."
    exec su-exec "${PUID}:${PGID}" python3 /opt/app/main.py
fi

echo "Facturen App wordt gestart op poort ${PORT} (tijdzone ${TZ})..."
exec python3 /opt/app/main.py
