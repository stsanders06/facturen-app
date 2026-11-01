#!/bin/sh
# Start de Facturen App als losse container (zonder Home Assistant).
set -eu

# Dezelfde standaarden als in Dockerfile.standalone, voor wie zonder compose start.
: "${PUID:=1000}" "${PGID:=1000}" "${PORT:=8099}" "${TZ:=Europe/Amsterdam}" "${DATA_DIR:=/data}"

# Een getal, en geen 0: met PUID=0 zou de app als root draaien, en dan kan een
# fout in de app meteen alles in de container. Liever niet starten.
for naam in PUID PGID; do
    eval "waarde=\${${naam}}"
    case "${waarde}" in
        ''|*[!0-9]*)
            echo "${naam}=${waarde} is geen getal. Zet in .env het nummer van je gebruiker (id -u / id -g)." >&2
            exit 1 ;;
    esac
    if [ "${waarde}" -eq 0 ]; then
        echo "${naam}=0 zou de app als root laten draaien. Zet in .env het nummer van je gebruiker (id -u / id -g)." >&2
        exit 1
    fi
done

# Docker maakt ./data zelf aan als die er nog niet is, en dan is root de
# eigenaar. Een gekopieerde administratie uit Home Assistant heeft ook vaak
# root als eigenaar. De app draait als PUID:PGID (standaard 1000, de eerste
# gebruiker op een Raspberry Pi), dus die moet erin kunnen schrijven. Alleen
# wat nog niet klopt wordt aangepast, zodat een herstart snel blijft.
# -h: een symlink zelf, niet het bestand waar hij naar wijst.
if [ "$(id -u)" = "0" ]; then
    mkdir -p "${DATA_DIR}"
    find "${DATA_DIR}" \( ! -user "${PUID}" -o ! -group "${PGID}" \) \
        -exec chown -h "${PUID}:${PGID}" {} +
    echo "Facturen App wordt gestart op poort ${PORT} (tijdzone ${TZ})..."
    exec su-exec "${PUID}:${PGID}" python3 /opt/app/main.py
fi

# Gestart als gewone gebruiker (user: in compose): dan kan en hoeft er niets
# gechownd te worden; de map data moet dan al van die gebruiker zijn.
echo "Facturen App wordt gestart op poort ${PORT} (tijdzone ${TZ})..."
exec python3 /opt/app/main.py
