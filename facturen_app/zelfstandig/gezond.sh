#!/bin/sh
# HEALTHCHECK van Dockerfile.standalone. /inloggen antwoordt altijd (200, of
# 302 naar /instellen zonder account). De poort komt uit PORT, zodat een andere
# POORT in .env geen "unhealthy" geeft. Als root zakt de controle naar nobody:
# hij hoeft niets te kunnen. Met user: in compose draait hij al als die gebruiker.
set -eu
CONTROLE="import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/inloggen' % os.environ.get('PORT', '8099'), timeout=4)"
if [ "$(id -u)" = "0" ]; then
    exec su-exec nobody python3 -c "${CONTROLE}"
fi
exec python3 -c "${CONTROLE}"
