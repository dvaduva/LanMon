# LanMon

Aplicație web pentru rețeaua de acasă: descoperă dispozitivele, arată dacă sunt pornite, le
organizează pe camere și le pornește prin Wake-on-LAN. Rulează ca un singur container Docker pe
QNAP, accesat din afară prin Tailscale. Specificația completă e în `docs/`.

Stare: **Faza 4** (actualizare live, Setări, backup, PWA), gata pentru release 1.0. Progresul pe
faze e bifat în `docs/`.

## Dezvoltare locală

```bash
python -m venv .venv
. .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

ruff check . && ruff format --check .
pytest

# Server local (datele în ./data; altă locație cu LANMON_DATA_DIR)
python -m app
# http://127.0.0.1:8765
```

Pe Windows, scanarea ARP are nevoie de [Npcap](https://npcap.com) (cu „WinPcap API-compatible
mode”), iar ARP-ul și ping-ul de un PowerShell pornit ca Administrator. Fără ele aplicația
pornește, iar Dashboard-ul arată de ce nu merge scanarea. Actualizarea IP-ului din tabela ARP
funcționează doar pe Linux (QNAP).

Migrările Alembic rulează automat la pornire. O migrare nouă, după schimbarea modelelor:

```bash
alembic revision --autogenerate -m "descriere"
```

## Release

Push pe `main` rulează ruff și pytest. Un tag `v*` construiește imaginea `linux/amd64` și o
publică în `ghcr.io/dvaduva/lanmon:<versiune>` și `:latest`:

```bash
git tag v1.0.0 && git push origin v1.0.0
```

## Instalare pe QNAP (o singură dată)

1. Creează folderul `/share/Container/lanmon/` și subfolderul `data/`.
2. Copiază acolo `docker-compose.yml` și `.env` (pornind de la `.env.example`; completează
   `LANMON_SECRET_KEY`).
3. Prin SSH pe QNAP, autentificare la GHCR cu un Personal Access Token cu drept `read:packages`:
   ```bash
   docker login ghcr.io -u dvaduva
   ```
4. Pornește:
   ```bash
   cd /share/Container/lanmon
   docker compose pull && docker compose up -d
   curl http://127.0.0.1:8765/health
   ```

## Validare rețea (Faza 0)

Din container, cu IP-ul și MAC-ul unui PC cu WOL activ (oprit pentru testul WOL):

```bash
docker exec -it lanmon python -m app.tools.netcheck arp
docker exec -it lanmon python -m app.tools.netcheck ping 192.168.1.10
docker exec -it lanmon python -m app.tools.netcheck wol aa:bb:cc:dd:ee:ff --wait-ip 192.168.1.10
```

Dacă interfața sau subnetul detectate nu sunt cele bune, adaugă `--interface eth0` sau
`--subnet 192.168.1.0/24` înaintea comenzii (sau setează `LANMON_INTERFACE` / `LANMON_SUBNET`).

Checklist dacă PC-ul nu pornește: WOL activ în BIOS/UEFI, „Wake on Magic Packet” activ în
driverul plăcii de rețea, Fast Startup dezactivat în Windows, PC conectat prin cablu. Dacă PC-ul
pornit nu răspunde la ping: activează regula de firewall „File and Printer Sharing (Echo Request -
ICMPv4-In)” pe profilul Private.

## Actualizare

```bash
cd /share/Container/lanmon
docker compose pull && docker compose up -d
```

## Prima pornire

La prima deschidere, aplicația cere crearea contului de administrator. Dacă
`LANMON_SECRET_KEY` lipsește, se generează o cheie în `secret.key`, în folderul de date (`/data` în container) (sesiunile rămân valide
la restart). Cu HTTPS (`tailscale serve`), setează `LANMON_COOKIE_SECURE=true`.

## Setări

Pagina **Setări** schimbă intervalul de ping (implicit 30 s), intervalul scanării ARP (300 s),
timeout-ul verificării WOL (180 s) și subnetul scanat (gol = detectare automată; are prioritate
față de `LANMON_SUBNET`). Intervalele noi se aplică de la următorul ciclu; un subnet nou se aplică
după restartul containerului. Tot acolo: schimbarea parolei și backup-ul.

Istoricul de stare și jurnalul de acțiuni păstrează 90 de zile; curățarea rulează noaptea la 03:30.

## Notificări

Setări → Notificări. Canale:

- **Telegram:** creează un bot cu @BotFather și copiază token-ul. Trimite-i botului un mesaj,
  apoi deschide `https://api.telegram.org/bot<TOKEN>/getUpdates`; numărul din `"chat":{"id":…}`
  e Chat ID-ul.
- **Email:** orice server SMTP. Pentru Gmail: `smtp.gmail.com`, port 587, STARTTLS și o
  [parolă de aplicație](https://myaccount.google.com/apppasswords).

Butonul „Trimite un mesaj de test” verifică imediat configurarea. Reguli: un dispozitiv cu
„Notifică-mă când e oprit” bifat în fișă e oprit de mai mult de pragul setat (și când revine),
un dispozitiv nou necunoscut apare în rețea, o pornire WOL eșuează. Token-ul și parola stau în
baza de date din `/data`, deci și în backup.

## Programări WOL

Setări → Programări WOL (sau linkul din cardul Wake-on-LAN al unui dispozitiv): un dispozitiv
sau toată camera, la o oră și în anumite zile. Ora e în fusul `LANMON_TZ` (implicit
Europe/Bucharest). Pornesc doar dispozitivele cu WOL care nu sunt deja pornite; o programare
ratată pentru că aplicația era oprită mai rulează doar dacă au trecut cel mult 10 minute.
Pornirile apar în Jurnal ca „programat”.

## Verificări pe porturi TCP

Pe lângă ping, LanMon poate verifica dacă un serviciu răspunde: porturi implicite pe tip
(Camere → Tipuri, ex. `3389` la PC pentru Remote Desktop) sau proprii în fișa dispozitivului
(au întâietate; `-` dezactivează verificarea). Porturile se verifică doar când dispozitivul
răspunde la ping, la același interval. După două verificări la rând cu porturi închise apare
„Serviciu căzut” pe Dashboard și în fișă; pentru dispozitivele cu notificări vine și un mesaj.

## Disponibilitate

Butonul „Disponibilitate” de pe Dashboard deschide o pagină cu procentul de timp în care
fiecare dispozitiv a fost pornit în ultimele 7 și 30 de zile, plus o bară pe zile (verde
închis = pornit toată ziua, portocaliu = mai puțin de jumătate, gri = fără date). Fișa
dispozitivului arată aceleași procente și ultimele 7 zile. Calculul folosește istoricul de
stare, în fusul orar `LANMON_TZ`; timpul dinainte de prima înregistrare nu se ia în calcul.

## Oprire de la distanță

LanMon poate opri un PC prin SSH, cu o cheie ed25519 generată la prima pornire (fișierul
`ssh_key` din directorul de date; păstreaz-o la backup, altfel trebuie adăugată din nou pe PC-uri).

1. Copiază cheia publică din **Setări → Oprire de la distanță**.
2. Adaug-o pe PC:
   - **Windows 10/11:** Setări → Sistem → Caracteristici opționale → *OpenSSH Server*, apoi
     în PowerShell ca administrator: `Start-Service sshd; Set-Service sshd -StartupType Automatic`.
     Pentru un cont de administrator, cheia merge în `C:\ProgramData\ssh\administrators_authorized_keys`
     (apoi `icacls C:\ProgramData\ssh\administrators_authorized_keys /inheritance:r /grant "Administrators:F" /grant "SYSTEM:F"`);
     pentru un cont obișnuit, în `C:\Users\<nume>\.ssh\authorized_keys`. Comanda rulată: `shutdown /s /t 0`.
   - **Linux / macOS:** cheia în `~/.ssh/authorized_keys` (pe macOS activează *Remote Login*).
     Comanda rulată e `sudo -n shutdown -h now`, deci utilizatorul are nevoie de o regulă sudo fără
     parolă, ex. în `/etc/sudoers.d/lanmon`: `dan ALL=(root) NOPASSWD: /sbin/shutdown, /usr/sbin/shutdown`.
3. În fișa dispozitivului bifează „Permite oprirea de la distanță”, completează utilizatorul SSH
   și sistemul de operare.

Butonul „Oprește” apare în fișă cât timp dispozitivul e pornit și cere confirmare. La prima
conectare LanMon memorează amprenta SSH a PC-ului; dacă se schimbă (ex. sistem reinstalat),
oprirea e refuzată până bifezi „Uită amprenta SSH memorată” în fișă. Fiecare oprire apare în
Jurnal. Membrii pot opri dispozitivele din camerele lor.

## Utilizatori

Setări → Utilizatori (doar pentru administratori). Un **administrator** vede și modifică tot.
Un **membru** vede pe Dashboard doar dispozitivele din camerele bifate la contul lui, le poate
deschide fișa și le poate porni; nu are acces la Neasignate, Camere, Jurnal, Programări sau
Setări (își poate schimba doar parola). Contul creat la prima pornire e administrator și
trebuie să rămână mereu cel puțin un administrator.

## Home Assistant (API REST)

Creează un token în **Setări → Home Assistant (API)**; se afișează o singură dată. Toate
cererile au antetul `Authorization: Bearer <token>`:

| Metodă | Cale | Ce face |
| --- | --- | --- |
| GET | `/api/v1/devices` | lista dispozitivelor, cu `online`, `status`, `ip`, `room`, `service_status` |
| GET | `/api/v1/devices/{id}` | un dispozitiv (id-ul e în adresa fișei: `/devices/{id}`) |
| POST | `/api/v1/devices/{id}/wake` | Wake-on-LAN (409 dacă nu e activat) |
| POST | `/api/v1/devices/{id}/shutdown` | oprire prin SSH (409 dacă nu e activată) |

Acțiunile apar în Jurnal cu sursa „Home Assistant”. Exemplu pentru `configuration.yaml`
(tokenul în `secrets.yaml` ca `lanmon_auth: "Bearer lm_..."`):

```yaml
rest:
  - resource: http://qnap.local:8765/api/v1/devices/1
    headers:
      Authorization: !secret lanmon_auth
    scan_interval: 30
    binary_sensor:
      - name: PC birou
        device_class: power
        value_template: "{{ value_json.online }}"

rest_command:
  pc_birou_wake:
    url: http://qnap.local:8765/api/v1/devices/1/wake
    method: post
    headers:
      Authorization: !secret lanmon_auth
  pc_birou_shutdown:
    url: http://qnap.local:8765/api/v1/devices/1/shutdown
    method: post
    headers:
      Authorization: !secret lanmon_auth

template:
  - switch:
      - name: PC birou
        state: "{{ is_state('binary_sensor.pc_birou', 'on') }}"
        turn_on:
          action: rest_command.pc_birou_wake
        turn_off:
          action: rest_command.pc_birou_shutdown
```

Înlocuiește `qnap.local:8765` cu adresa la care rulează LanMon (sau adresa Tailscale).

## Backup și restaurare

- **Manual:** Setări → „Descarcă backup” salvează o copie completă `lanmon-<data>.db` (dispozitive,
  camere, tipuri, istoric, jurnal, cont).
- **Automat:** înainte de fiecare migrare a schemei, aplicația pune o copie în
  `/data/backups/pre-migrare-*.db` (se păstrează ultimele 5).

Restaurare (inclusiv pe o instalare nouă):

```bash
cd /share/Container/lanmon
docker compose down
cp lanmon-2026-10-25.db data/lanmon.db
rm -f data/lanmon.db-wal data/lanmon.db-shm
docker compose up -d
```

Contul de administrator vine din backup. Dacă `secret.key` (sau `LANMON_SECRET_KEY`) diferă, doar
sesiunile deschise expiră și te autentifici din nou.

## Aplicație pe telefon (PWA)

Deschide LanMon în browser și alege „Add to Home Screen” (Safari: butonul Share; Chrome: meniul ⋮
→ „Install app”). Se deschide pe tot ecranul, cu iconița proprie. Instalarea completă (cu service
worker) cere HTTPS, adică `tailscale serve`; pe `http://` scurtătura funcționează, dar fără mod
offline pentru fișierele statice.

## Acces de la distanță

Tailscale instalat din App Center pe QNAP și pe telefon. Adresa: `http://<nume-qnap>:8765` prin
MagicDNS. Verifică după un restart QTS că Tailscale pornește singur.
