# LanMon – Specificații tehnice și plan de implementare

Oct 25, 2026 · @Daniel Vaduva

## Rezumat

LanMon este o aplicație web pentru rețeaua de acasă: descoperă dispozitivele, arată dacă sunt pornite, le organizează pe camere și le pornește de la distanță prin Wake-on-LAN. Rulează ca un singur container Docker pe QNAP TS-453A și e accesată din afara casei prin Tailscale. Numele LanMon e provizoriu.

| Subiect | Decizie |
| --- | --- |
| Utilizare | În familie, o singură locație |
| Rețea | Câteva zeci de dispozitive, un singur VLAN/subnet |
| Platformă | Docker în Container Station, QNAP TS-453A (x86\_64), `network_mode: host` |
| Stack | Python 3.12, FastAPI, Jinja2 + HTMX, SSE, SQLite |
| Identificare dispozitiv | Adresa MAC (IP-ul se actualizează automat) |
| Descoperire | Scanare ARP la 5 minute + buton „Scanează acum”; dispozitivele noi intră în „Neasignate” |
| Status „on” | Răspunde la ping; ping la 30 s pentru dispozitivele salvate |
| Camere și tipuri | Un dispozitiv într-o singură cameră; tipuri predefinite, editabile, cu iconiță |
| Wake-on-LAN | Activat per dispozitiv; buton per dispozitiv și „Pornește toată camera”; verificare prin ping max. 3 min |
| Acces | Un cont admin; acces de la distanță doar prin Tailscale, fără porturi deschise |
| Istoric | Evenimente de schimbare de stare + jurnal acțiuni WOL, retenție 90 de zile |
| Livrare | GitHub + GitHub Actions → imagine `amd64` în GHCR privat; update cu `docker compose pull` |
| Port aplicație | 8765 (8080 e ocupat de QTS) |

## Arhitectură

&#91;embedded content: arhitectură · un container, Tailscale separat\]

Tot ce atinge rețeaua rulează în containerul de pe QNAP. Telefonul trimite doar cereri HTTP prin tunelul Tailscale. Scanerul, ping-ul și WOL sunt task-uri asyncio în același proces cu serverul web, pornite din `lifespan`. Un bus intern de evenimente transmite schimbările de stare către SSE.

```text
app/
  main.py          FastAPI, lifespan pornește task-urile
  config.py        setări din .env (pydantic-settings)
  web/             rute, șabloane Jinja2, fragmente HTMX, SSE
  services/        scanner.py, pinger.py, wol.py, retention.py
  db/              modele SQLAlchemy, migrări Alembic
  events.py        bus intern de evenimente
```

## Model de date (SQLite)

Cheia naturală a unui dispozitiv este adresa MAC, normalizată la `aa:bb:cc:dd:ee:ff`. Baza rulează în modul WAL, ca scanerul și interfața web să poată scrie și citi simultan. Schema e gestionată cu SQLAlchemy 2 + Alembic.

| Tabel | Câmpuri principale | Note |
| --- | --- | --- |
| `devices` | `id`, `mac` (unic), `name`, `room_id` (nullable), `type_id`, `last_ip`, `hostname`, `vendor`, `wol_enabled`, `status` (online/offline/unknown), `last_seen_at`, `status_changed_at`, `is_random_mac`, `notes`, `created_at` | Doar dispozitivele salvate de admin |
| `discovered` | `mac` (PK), `ip`, `hostname`, `vendor`, `first_seen_at`, `last_seen_at`, `ignored` | Lista „Neasignate”; un MAC salvat dispare de aici |
| `rooms` | `id`, `name` (unic), `sort_order`, `icon` | „Fără cameră” = `room_id` NULL |
| `device_types` | `id`, `name`, `icon`, `sort_order`, `is_builtin` | Precompletat: PC, laptop, TV, NAS, imprimantă, telefon, tabletă, IoT, router/AP, altele |
| `status_events` | `id`, `device_id`, `status`, `ip`, `at` | Doar la schimbarea stării; retenție 90 de zile |
| `actions_log` | `id`, `device_id`, `action` (wol/scan), `source` (user/room), `result` (sent/woke/timeout/error), `detail`, `created_at`, `finished_at` | Jurnal WOL și scanări manuale |
| `settings` | `key`, `value` | Interval ping, interval scanare, subnet, timeout WOL |
| `users` | `id`, `username`, `password_hash` (argon2), `created_at`, `last_login_at` | Un singur rând în faza 1 |

Vendorul se deduce din primii 3 octeți ai MAC-ului, folosind o bază OUI inclusă în imagine. Un MAC cu al doilea bit al primului octet setat e marcat automat `is_random_mac`.

## Specificații funcționale

### Descoperire dispozitive

- Scanare ARP pe tot subnetul configurat (ex. `192.168.1.0/24`) cu scapy, la 5 minute și la cerere („Scanează acum”).
- Subnetul și interfața se detectează automat la prima pornire și pot fi suprascrise din Setări.
- Pentru fiecare răspuns: MAC, IP, hostname (reverse DNS, apoi NetBIOS/mDNS dacă e disponibil), vendor din OUI.
- MAC nou → apărea în „Neasignate”. MAC salvat → se actualizează `last_ip` și `last_seen_at`.
- „Ignoră” ascunde un MAC din listă permanent; se poate reactiva din Setări.

### Status (online / offline)

- Ping ICMP la 30 s, în paralel, pentru toate dispozitivele salvate (icmplib async, socket raw).
- Se folosește `last_ip`; dacă ping-ul eșuează, se verifică tabela ARP ca să prindă un IP schimbat de DHCP.
- Anti-flapping: un dispozitiv devine „offline” după 2 eșecuri consecutive și „online” la primul răspuns.
- Fiecare schimbare de stare scrie un rând în `status_events` și e trimisă live către browsere prin SSE.
- Starea „unknown” apare doar înainte de primul ciclu de ping.

### Camere și tipuri

- CRUD pentru camere (nume, iconiță, ordine) și tipuri (nume, iconiță, ordine).
- O cameră sau un tip folosit nu se poate șterge; se cere mutarea dispozitivelor întâi.
- Fiecare dispozitiv: nume, cameră, tip, checkbox „suportă WOL”, note libere.

### Wake-on-LAN

- Pachet magic: 6 × `0xFF` + MAC repetat de 16 ori, UDP pe porturile 9 și 7, trimis la broadcast-ul subnetului și la `255.255.255.255`.
- „Pornește” pe dispozitiv și „Pornește toată camera” (doar dispozitivele cu WOL bifat și offline).
- După trimitere, ping la 5 s timp de cel mult 3 minute. UI arată „Se pornește…”, apoi „Pornit” sau „Nu a răspuns”.
- La timeout, UI afișează indicii: WOL activ în BIOS/UEFI, alimentare, „Wake on Magic Packet” în driverul plăcii de rețea, Fast Startup dezactivat în Windows.
- Fiecare trimitere e scrisă în `actions_log` cu rezultatul final.

### Istoric

- Fișa dispozitivului: „online de la …” / „offline de la …” și ultimele 20 de evenimente.
- Pagina Jurnal: acțiunile WOL și scanările, cele mai noi primele.
- Job nocturn (03:30) care șterge evenimentele mai vechi de 90 de zile.

### Autentificare și securitate

- Un cont admin, creat la prima pornire printr-un ecran de configurare; parolă cu argon2.
- Sesiune în cookie semnat, `HttpOnly`, `SameSite=Lax`; `Secure` când aplicația e servită prin HTTPS.
- Protecție CSRF pe toate acțiunile POST.
- Rate limiting la login: 5 încercări eșuate, apoi blocare 15 minute.
- Aplicația nu se expune la internet; accesul din afară se face doar prin Tailscale.

### Interfața web

Mobile-first, server-rendered cu Jinja2 + HTMX, fără build de frontend. Pagini:

1. **Dashboard** — dispozitivele grupate pe camere, cu indicator de stare, iconița tipului și buton „Pornește”; contor online/total per cameră.
2. **Fișa dispozitivului** — detalii, editare, istoric.
3. **Neasignate** — dispozitive descoperite, cu „Adaugă” și „Ignoră”.
4. **Camere** și **Tipuri** — administrare.
5. **Jurnal** — acțiuni WOL și scanări.
6. **Setări** — subnet, intervale, timeout WOL, schimbare parolă, backup.

Statusul se actualizează live prin SSE. Aplicația are manifest PWA și se poate adăuga pe ecranul principal al telefonului.

## Deployment pe QNAP TS-453A

Aplicația e un singur container, cu rețea de tip host și doar capabilitatea `NET_RAW`, cu datele pe un share QNAP. Tailscale e instalat separat, din App Center.

### Imagine și CI/CD

- Repository privat pe GitHub; imagine de bază `python:3.12-slim`, platformă `linux/amd64`.
- GitHub Actions: la push pe `main` rulează lint (ruff) și teste (pytest); la tag `v*` construiește și publică `ghcr.io/<cont>/lanmon:<versiune>` și `:latest`.
- Pe QNAP se face o singură dată `docker login ghcr.io` cu un token cu drept `read:packages`.

### docker-compose.yml

```yaml
services:
  lanmon:
    image: ghcr.io/<cont>/lanmon:latest
    container_name: lanmon
    network_mode: host
    cap_drop: [ALL]
    cap_add: [NET_RAW]
    restart: unless-stopped
    env_file: .env
    volumes:
      - /share/Container/lanmon/data:/data
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/health')"]
      interval: 60s
```

`.env` conține: `LANMON_PORT=8765`, `LANMON_SUBNET` (opțional, altfel auto), `LANMON_INTERFACE` (opțional), `LANMON_SECRET_KEY`, `TZ=Europe/Bucharest`.

Uvicorn rulează cu un singur worker, pentru că scanerul și ping-ul sunt task-uri de fundal în același proces.

### Actualizare

1. Publici un tag nou pe GitHub și aștepți build-ul.
2. Pe QNAP: `docker compose pull && docker compose up -d`.
3. Migrările Alembic rulează automat la pornire; înainte de migrare aplicația salvează o copie a bazei în `/data/backups/`.

### Backup

- Folderul `/share/Container/lanmon/data` intră în jobul Hybrid Backup Sync existent.
- În Setări, „Descarcă backup” generează un snapshot consistent cu API-ul de backup SQLite.

### Acces de la distanță

- Tailscale instalat pe QNAP din App Center și pe telefon/laptop.
- Adresă de acces: `http://<nume-qnap>:8765` prin MagicDNS.
- Opțional HTTPS: `tailscale serve --bg --https=8443 http://127.0.0.1:8765`, apoi `https://<nume-qnap>.<tailnet>.ts.net:8443`. Portul 8443 evită conflictul cu 443 al QTS.

## Plan de implementare pe faze

> **Stare (29 oct 2026):** Fazele 0–5 sunt implementate pe `main`; versiunea 1.0 e gata de release. Faza 6 (backlog-ul de după 1.0) și Faza 7 (multi-limbă) sunt implementate și acoperite de teste. Criteriile care depind de rețeaua de acasă se verifică pe QNAP la final.

Versiunea 1.0 se livrează în 5 faze, cu aproximativ 14–19 zile de lucru pentru un dezvoltator (estimare). Faza 0 verifică devreme cel mai mare risc: dacă ARP, ping-ul și broadcast-ul WOL funcționează din container pe QNAP. Fiecare fază se încheie cu o versiune instalată pe QNAP.

### Faza 0 — Fundament și validare tehnică (2–3 zile)

Livrabile:

- [x] Repository, structură de proiect, ruff + pytest, configurare din `.env` (pydantic-settings).
- [x] Schelet FastAPI cu `/health`, SQLite în WAL, primele migrări Alembic.
- [x] Dockerfile, `docker-compose.yml`, workflow GitHub Actions care publică imaginea în GHCR.
- [x] Script de test în container: o scanare ARP, un ping și un pachet WOL către un PC real (`python -m app.tools.netcheck`).
- [ ] Tailscale instalat pe QNAP și pe telefon.

Criterii de acceptanță:

- [ ] Containerul rulează pe QNAP din imaginea GHCR și `/health` răspunde. *(de verificat pe QNAP, la final)*
- [ ] Scriptul de test găsește dispozitivele prin ARP și primește răspuns la ping din container. *(de verificat pe QNAP, la final)*
- [ ] Un PC oprit pornește cu pachetul WOL trimis din container. *(de verificat pe QNAP, la final)*
- [ ] `/health` răspunde de pe telefon, prin Tailscale, cu Wi-Fi-ul oprit. *(de verificat pe QNAP, la final)*

### Faza 1 — Descoperire și status (3–4 zile)

Livrabile:

- [x] Serviciu de scanare ARP periodică și la cerere; populare `discovered`; vendor din OUI; detecție MAC randomizat.
- [x] Serviciu de ping la 30 s, cu anti-flapping și actualizare IP din tabela ARP.
- [x] Scriere `status_events` la schimbările de stare.
- [x] Teste unitare pentru normalizarea MAC, logica de stare și construcția pachetului WOL.

Criterii de acceptanță:

- [ ] După o scanare, toate dispozitivele pornite din rețea apar în `discovered`. *(de verificat pe QNAP, la final)*
- [ ] Un PC oprit devine „offline” în cel mult 90 de secunde, iar la pornire „online” în cel mult 30 de secunde după ce răspunde la ping. *(de verificat pe QNAP, la final)*
- [ ] Un IP schimbat de DHCP e preluat automat pentru același MAC. *(de verificat pe QNAP, la final)*

### Faza 2 — Autentificare și administrare (4–5 zile)

Livrabile:

- [x] Ecran de configurare la prima pornire, login, logout, schimbare parolă, rate limiting, CSRF.
- [x] Layout mobile-first (Jinja2 + HTMX), Dashboard pe camere.
- [x] Pagini Neasignate (Adaugă / Ignoră), fișă dispozitiv cu editare, Camere, Tipuri.
- [x] Tipuri predefinite și iconițe.

Criterii de acceptanță:

- [x] Nicio pagină și nicio acțiune nu e accesibilă fără login. *(verificat prin teste)*
- [ ] Un dispozitiv din Neasignate poate fi salvat cu nume, cameră și tip în sub 30 de secunde de pe telefon. *(de verificat pe QNAP, la final)*
- [ ] Dashboard-ul arată starea corectă pentru toate dispozitivele salvate. *(de verificat pe QNAP, la final)*

### Faza 3 — Wake-on-LAN (2–3 zile)

Livrabile:

- [x] Trimitere WOL per dispozitiv și „Pornește toată camera”.
- [x] Verificare prin ping la 5 s, cel mult 3 minute, cu stările „Se pornește…”, „Pornit”, „Nu a răspuns”.
- [x] Jurnal `actions_log` și pagina Jurnal; indicii de depanare la timeout.

Criterii de acceptanță:

- [ ] Un PC oprit pornește de pe telefon, prin Tailscale, din afara casei, și apare „Pornit”. *(de verificat pe QNAP, la final)*
- [x] „Pornește toată camera” trimite pachete doar dispozitivelor cu WOL bifat și offline. *(verificat prin teste)*
- [x] Fiecare acțiune apare în Jurnal cu rezultatul final. *(verificat prin teste)*

### Faza 4 — Live, istoric și release 1.0 (3–4 zile)

Livrabile:

- [x] Actualizare live a stării prin SSE pe Dashboard și în fișa dispozitivului.
- [x] Istoric în fișă (ultimele 20 de evenimente, făcut în Faza 2), job de retenție 90 de zile.
- [x] Pagina Setări (subnet, intervale, timeout WOL), backup descărcabil, backup automat înainte de migrări.
- [x] Manifest PWA și iconiță de aplicație; README cu instalare și update.

Criterii de acceptanță:

- [ ] Starea se schimbă pe ecran fără refresh când un dispozitiv pornește sau se oprește. *(verificat în browser; de confirmat pe QNAP, la final)*
- [ ] Aplicația se adaugă pe ecranul principal și se deschide ca aplicație. *(de verificat pe telefon, la final)*
- [x] Restaurarea unui backup pe o instalare nouă readuce dispozitivele, camerele și istoricul. *(verificat prin teste)*
- [ ] Un update de versiune se face doar cu `docker compose pull && docker compose up -d`, fără pierdere de date. *(de verificat pe QNAP, la final)*

### Faza 5 — Îmbunătățiri după 1.0

Livrabile:

- [x] Camere colapsabile pe Dashboard; starea se păstrează în browser.
- [x] Dashboard afișat ca listă sau ca tile-uri, la alegere.
- [x] Layout pe toată lățimea ecranului; pe ecrane late, cardurile din Setări și din fișă stau pe coloane.
- [x] Rulare locală pe Windows: date implicit în `./data`, mesaj clar (fără traceback) când lipsește Npcap, teste stabile pe Windows.
- [x] Npcap instalat pe PC-ul de dezvoltare.

### Faza 6 — Backlog după 1.0

Livrabile, în ordinea de lucru:

- [x] **Notificări:** Telegram (bot) și email (SMTP), configurate din Setări, cu mesaj de test. Reguli: dispozitiv marcat „cu notificări” offline de cel puțin X minute (și revenit), dispozitiv nou necunoscut în rețea, pornire WOL eșuată.
- [x] **WOL programat:** programări pe dispozitiv sau pe cameră (oră + zile ale săptămânii), în fusul orar `LANMON_TZ` (implicit Europe/Bucharest); apar în Jurnal cu sursa „program”.
- [x] **Verificări pe porturi TCP:** porturi implicite pe tip (ex. PC 3389, NAS 443) și suprascriere pe dispozitiv; la dispozitivele care răspund la ping se verifică porturile, iar starea „rețea OK, serviciu căzut” apare pe Dashboard și în fișă.
- [x] **Mai mulți utilizatori, cu roluri:** rolurile „administrator” și „membru”; un membru vede și pornește doar dispozitivele din camerele permise. Administratorul gestionează utilizatorii din Setări.
- [x] **Grafice de uptime:** disponibilitatea pe ultimele 7 și 30 de zile, calculată din `status_events`, în fișa dispozitivului și într-o pagină de ansamblu.
- [x] **Oprire de la distanță:** prin SSH, cu o cheie generată de aplicație (cheia publică se copiază pe PC); comandă după sistemul de operare (Windows, Linux, macOS); confirmare înainte de oprire; apare în Jurnal.
- [x] **Integrare Home Assistant:** API REST cu token (`/api/v1`): lista dispozitivelor cu starea, pornire și oprire; exemple de configurare HA în README.

Criterii de acceptanță:

- [ ] Un mesaj de test ajunge pe Telegram și pe email; un PC marcat oprit mai mult de X minute generează o notificare. *(de verificat pe QNAP, la final)*
- [ ] Un PC programat pentru 8:00 luni–vineri pornește la ora stabilită. *(de verificat pe QNAP, la final)*
- [ ] Un PC pornit cu serviciul RDP oprit apare „serviciu căzut”. *(de verificat pe QNAP, la final)*
- [x] Un membru vede doar camerele permise și nu poate accesa paginile de administrare. *(verificat prin teste)*
- [x] Fișa dispozitivului arată disponibilitatea pe 7 și 30 de zile. *(verificat prin teste)*
- [ ] Un PC Windows cu OpenSSH se oprește din aplicație. *(de verificat pe QNAP, la final)*
- [ ] Home Assistant afișează starea unui PC și îl poate porni prin API. *(de verificat la final)*

### Faza 7 — Multi-limbă

Interfața în engleză și română, cu limba aleasă per utilizator. Codul sursă și README-ul sunt în engleză; specificația rămâne în română.

- [x] Engleza e limba sursă: textele din cod și din șabloane trec prin traducere (`t()`, `_()`, `ngettext`), iar româna stă în `app/locales/ro.json` (cu forme de plural: 1, 2–19, 20+).
- [x] Limba se alege per utilizator (Setări → Limbă; administratorul o poate seta și din Utilizatori); paginile de autentificare și configurare urmează browserul sau linkurile English · Română.
- [x] Migrarea păstrează instalările existente în română și dă nume englezești tipurilor predefinite (traduse la afișare).
- [x] Notificările (Telegram, email) pleacă în limba primului administrator; API-ul răspunde în engleză.
- [x] Codul sursă (comentarii, mesaje de log, unelte) și README-ul traduse în engleză.

Criterii de acceptanță:

- [x] Fiecare text din interfață are traducere în română; testul eșuează dacă lipsește o traducere sau dacă una nu mai e folosită. *(verificat prin teste)*
- [x] Doi utilizatori văd aplicația fiecare în limba lui. *(verificat prin teste)*

## Faze viitoare (backlog)

Backlog-ul inițial, păstrat pentru context. Toate elementele de mai jos au fost implementate în Faza 6.

| Funcționalitate | Ce adaugă | Impact asupra arhitecturii |
| --- | --- | --- |
| Notificări | Telegram (bot) sau email: dispozitiv offline de X minute, dispozitiv nou necunoscut | Un serviciu de notificare + reguli în `settings` |
| WOL programat | Ex. „PC birou la 8:00, luni–vineri” | Tabel `schedules` + scheduler propriu în `lifespan` |
| Verificări suplimentare de stare | Porturi TCP per tip de dispozitiv (RDP 3389, SSH 22, HTTP 80), stare „rețea OK, serviciu căzut” | Câmp de metodă de verificare pe tip/dispozitiv |
| Mai mulți utilizatori și roluri | „Membru familie” vede statusul și pornește doar anumite dispozitive sau camere | Tabele `roles` și permisiuni pe cameră/dispozitiv |
| Grafice de disponibilitate | Uptime pe zi/săptămână per dispozitiv | Agregări din `status_events` |
| Oprire de la distanță | Shutdown pentru PC-uri (agent mic sau SSH) | Agent pe PC sau credențiale stocate criptat |
| Integrare Home Assistant | Expunere stare și WOL prin MQTT sau REST | Un modul de API cu token |

## Riscuri și puncte de verificat

| Risc | Efect | Măsură |
| --- | --- | --- |
| Socket raw sau broadcast blocat în containerul de pe QNAP | Scanarea, ping-ul sau WOL nu funcționează | Validare în Faza 0, înainte de orice altceva |
| Windows blochează ping-ul implicit | PC pornit apărut „offline” | Regulă de firewall „ICMPv4 Echo Request” pe profilul Private; notă în README |
| Fast Startup în Windows sau WOL dezactivat în BIOS | PC-ul nu pornește la pachetul magic | Indicii de depanare la timeout; checklist în README |
| Dispozitive pe Wi-Fi | WOL rareori funcționează | WOL recomandat doar pentru dispozitive pe cablu |
| Telefoane în standby sau cu MAC randomizat | Apar offline sau ca dispozitive noi | Marcare `is_random_mac`; recomandare de MAC fix pentru rețeaua de acasă |
| Scrieri frecvente pe disc | Hard diskurile QNAP nu mai intră în hibernare | `last_seen_at` ținut în memorie și scris doar la schimbarea stării sau o dată la 15 minute |
| Tailscale oprit după un restart QTS | Acces de la distanță indisponibil | Verificare auto-start în Faza 0; test după restart |

Punct deschis: vrei ca hard diskurile QNAP să intre în hibernare? Dacă da, folderul de date poate sta pe un volum SSD, dacă există unul.
