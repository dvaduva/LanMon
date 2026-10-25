# LanMon

Aplicație web pentru rețeaua de acasă: descoperă dispozitivele, arată dacă sunt pornite, le
organizează pe camere și le pornește prin Wake-on-LAN. Rulează ca un singur container Docker pe
QNAP, accesat din afară prin Tailscale. Specificația completă e în `docs/`.

Stare: **Faza 1** (descoperire ARP și status prin ping, fără interfață web încă).

## Dezvoltare locală

```bash
python -m venv .venv
. .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

ruff check . && ruff format --check .
pytest

# Server local (datele în ./data)
LANMON_DATA_DIR=./data python -m app     # Windows PowerShell: $env:LANMON_DATA_DIR="./data"
# http://127.0.0.1:8765/health
```

Migrările Alembic rulează automat la pornire. O migrare nouă, după schimbarea modelelor:

```bash
LANMON_DATA_DIR=./data alembic revision --autogenerate -m "descriere"
```

## Release

Push pe `main` rulează ruff și pytest. Un tag `v*` construiește imaginea `linux/amd64` și o
publică în `ghcr.io/dvaduva/lanmon:<versiune>` și `:latest`:

```bash
git tag v0.1.0 && git push origin v0.1.0
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

## Acces de la distanță

Tailscale instalat din App Center pe QNAP și pe telefon. Adresa: `http://<nume-qnap>:8765` prin
MagicDNS. Verifică după un restart QTS că Tailscale pornește singur.
