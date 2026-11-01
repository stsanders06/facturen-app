# Facturen App zonder Home Assistant (Docker)

Naast de add-on voor Home Assistant draait de Facturen App ook als losse
container, bijvoorbeeld op een Raspberry Pi met Raspberry Pi OS Lite (64-bit).
Alles werkt hetzelfde: rekeningen, offertes, PDF's, mail en de scans vanaf je
iPhone. Alleen de zijbalk van Home Assistant is er niet; je opent de app in je
browser en logt in met je gebruikersnaam en wachtwoord.

Nodig: Docker met Compose (`docker compose version` laat een versie zien) en git.
Moet je `sudo` voor `docker` typen, zet jezelf dan eenmalig in de groep docker
(`sudo usermod -aG docker $USER`, daarna opnieuw inloggen); de opdrachten
hieronder gaan daarvan uit.

Alle opdrachten hieronder typ je op de Pi, tenzij er staat dat het in Home
Assistant moet.

## 1. Installeren

```sh
cd ~
git clone https://github.com/stsanders06/facturen-app.git
cd facturen-app
cp .env.example .env
mkdir -p data
docker compose up -d --build
```

Het eerste bouwen duurt op een Pi 5 een paar minuten. Daarna:

```sh
docker compose ps
```

Bij `STATUS` staat `Up ... (healthy)` als alles goed is. Open in je browser
`http://<adres-van-de-pi>:8099/` (het adres vind je met `hostname -I`). Bij een
lege administratie kies je eerst een gebruikersnaam en wachtwoord. Heb je je
administratie uit Home Assistant meegenomen (stap 2), dan log je in met het
account dat je daar al had.

### Instellingen in .env

| Naam | Standaard | Wat het doet |
| --- | --- | --- |
| `POORT` | `8099` | De poort op de Pi. Na een wijziging: `docker compose up -d`. |
| `TZ` | `Europe/Amsterdam` | Tijdzone voor datums en "te laat". |
| `LOG_LEVEL` | `info` | `debug`, `info`, `warning` of `error`. |
| `PUID` / `PGID` | `1000` | Eigenaar van de map `data`. Controleer met `id -u` en `id -g`. |

Mailserver, bedrijfsgegevens en logo staan níet in `.env`. Die zet je in de app
onder Instellingen, net als in Home Assistant. Ze staan in de database en
verhuizen vanzelf mee.

## 2. Je administratie uit Home Assistant meenemen

In de map `data` van de add-on staat alles: de database (`facturen.db`), de
PDF's, je logo, de bijlagen en foto's bij klussen, de scans met hun
miniaturen, en de sleutel voor de inlog (`secret_key`). Die map haal je via een
back-up uit Home Assistant.

### 2a. In Home Assistant: back-up van alleen de add-on maken

Open in Home Assistant de app **Terminal & SSH** (of Advanced SSH & Web
Terminal) en zoek eerst de naam (slug) van de add-on op:

```sh
ha addons | grep -i -B1 -A1 facturen
```

Die ziet eruit als `1a2b3c4d_facturen_app`. Je ziet hem ook in de adresbalk als
je de add-on opent: `.../hassio/addon/1a2b3c4d_facturen_app/info`. Vul hem
hieronder in bij `SLUG`.

Zet de add-on stil, zodat er tijdens het kopiëren niets verandert, en maak een
back-up van alleen deze add-on:

```sh
SLUG=1a2b3c4d_facturen_app
ha addons stop $SLUG
ha backups new --addons $SLUG --name facturen-verhuizing
ls -l /backup
```

Een back-up die je zo in de terminal maakt heeft geen wachtwoord. (Back-ups uit
het scherm Instellingen → Systeem → Back-ups zijn vaak versleuteld; die kun je
hiervoor niet gebruiken.)

### 2b. De back-up naar de Pi kopiëren

Nog steeds in de terminal van Home Assistant (vul de gebruiker en het adres van
de Pi in, en de bestandsnaam die `ls -l /backup` liet zien):

```sh
scp /backup/<bestand>.tar <gebruiker>@<adres-van-de-pi>:~/
```

Werkt `scp` niet, gebruik dan de app **Samba share**: daar staat de back-up in
de map `backup`, en kopieer hem zo naar je Mac en van daar naar de Pi.

### 2c. Op de Pi: uitpakken in ./data

```sh
cd ~/facturen-app
docker compose down
mkdir -p /tmp/verhuizing && cd /tmp/verhuizing
tar -xf ~/<bestand>.tar
ls
```

Je ziet `backup.json` en een bestand dat eindigt op `_facturen_app.tar.gz`.
Pak dat uit en zet de inhoud van `data` in de map van de app:

```sh
tar -xzf *_facturen_app.tar.gz
ls data
cp -a data/. ~/facturen-app/data/
cd ~/facturen-app
docker compose up -d
```

In `ls data` moet in elk geval `facturen.db` staan. Krijg je bij `tar -xzf` de
melding `not in gzip format`, dan is de back-up versleuteld: maak hem opnieuw
zoals in stap 2a, in de terminal.

De container maakt de bestanden bij het starten zelf van de juiste gebruiker;
`sudo` of `chown` is niet nodig.

### 2d. Controleren en afronden

1. Open `http://<adres-van-de-pi>:8099/` en log in. Kijk of je klanten,
   rekeningen, een PDF en je scans er zijn.
2. Laat de add-on in Home Assistant uit staan, en zet **Start bij opstarten**
   uit. Twee apps naast elkaar betekent twee administraties die uit elkaar
   lopen. Verwijder de add-on pas als alles een tijdje goed gaat; de back-up
   uit stap 2a blijft staan.
3. **iPhone-opdracht «Naar Facturen»**: het adres verandert. Open in de app
   Instellingen, kopieer het nieuwe adres (iets als
   `http://192.168.1.50:8099/api/scans`) en zet het in de opdracht. De code
   blijft dezelfde.
4. Ruim op: `rm -r /tmp/verhuizing`.

## 3. Bijwerken

```sh
cd ~/facturen-app
git pull && docker compose up -d --build
```

Je administratie in `data` blijft staan. Oude images opruimen:
`docker image prune -f`.

## 4. Back-up

Alles staat in de map `data`. Zet de app een paar seconden stil, zodat de
database niet halverwege een wijziging wordt gekopieerd:

```sh
cd ~/facturen-app
docker compose stop
tar -czf ~/facturen-backup-$(date +%F).tar.gz data
docker compose start
```

Zet het bestand daarna ergens anders dan op de SD-kaart van de Pi, bijvoorbeeld
op je Mac:

```sh
# op de Mac
scp <gebruiker>@<adres-van-de-pi>:~/facturen-backup-*.tar.gz ~/Documents/
```

Elke zondag om 03:00 automatisch (op de Pi `crontab -e` en deze regel toevoegen):

```cron
0 3 * * 0 cd ~/facturen-app && { docker compose stop; tar -czf ~/facturen-backup-$(date +\%F).tar.gz data; docker compose start; }
```

### Terugzetten

```sh
cd ~/facturen-app
docker compose down
mv data data-oud
tar -xzf ~/facturen-backup-JJJJ-MM-DD.tar.gz
docker compose up -d
```

## 5. Handig om te weten

- **Logboek**: `docker compose logs -f` (stoppen met Ctrl+C).
- **Wachtwoord kwijt**: `docker compose exec facturen wachtwoord-herstellen`.
  Je kiest een nieuw wachtwoord en ziet je gebruikersnaam.
- **Herstarten**: `docker compose restart`. Na een stroomstoring start de app
  vanzelf weer (`restart: unless-stopped`).
- **Stoppen**: `docker compose down`. Je administratie blijft in `data`.
- **Alleen thuis**: de app is bedoeld voor je eigen netwerk. Wil je er
  onderweg bij, gebruik dan een VPN naar huis (bijvoorbeeld Tailscale of
  WireGuard) in plaats van de poort open te zetten in je router.
- **Geheugen**: de app gebruikt rond de 50 MB; `mem_limit: 512m` in
  `docker-compose.yml` is de bovengrens.
