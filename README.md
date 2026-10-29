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
