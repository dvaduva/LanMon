# LanMon

A web app for the home network: it discovers devices, shows whether they're online, groups them
by room and wakes them with Wake-on-LAN. It runs as a single Docker container on a QNAP and is
reached from outside through Tailscale. The full specification (in Romanian) is in `docs/`.

The interface is available in English and Romanian; each user picks their language in Settings.

Status: phases 0–6 are implemented; progress per phase is ticked in `docs/`.

## Local development

```bash
python -m venv .venv
. .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

ruff check . && ruff format --check .
pytest

# Local server (data in ./data; another location with LANMON_DATA_DIR)
python -m app
# http://127.0.0.1:8765
```

On Windows, ARP scanning needs [Npcap](https://npcap.com) (with "WinPcap API-compatible mode"),
and ARP and ping need a PowerShell started as Administrator. Without them the app still starts
and the Dashboard explains why scanning doesn't work. Picking up IP changes from the ARP table
only works on Linux (QNAP).

Alembic migrations run automatically on startup. A new migration, after changing the models:

```bash
alembic revision --autogenerate -m "description"
```

## Translations

English is the source language: user-facing text is written in English in the code and wrapped
for translation, and the other languages live in `app/locales/<lang>.json`.

- Python: `t(request, "Saved {name}.", name=...)`, `tn(request, singular, plural, n)`, or
  `N_("...")` for text that is stored or defined up front and translated on display.
  Background services use `app.i18n.gettext(lang, ...)`.
- Templates: `{{ _("Text") }}`, `{{ _("Hi {name}", name=...) }}`,
  `{{ ngettext("{n} device", "{n} devices", count) }}`.
- In a catalog, a plural message maps to a list of forms (Romanian has three: 1, 2–19, 20+).

`tests/test_i18n.py` fails when a message is missing from `ro.json`, when `ro.json` has unused
entries, or when a translation uses a placeholder the English text doesn't have. To add a
language, add it to `LANGUAGES` in `app/i18n.py` (and its plural rule to `plural_index`), then
create `app/locales/<lang>.json` with the same keys as `ro.json`.

Language choice: a signed-in user's own setting (Settings → Language; administrators also set it
per user). The sign-in and setup pages follow the browser, or the English · Română links under
the form. Notifications are sent in the language of the first administrator.

## Release

A push to `main` runs ruff and pytest. A `v*` tag builds the `linux/amd64` image and publishes it
as `ghcr.io/dvaduva/lanmon:<version>` and `:latest`:

```bash
git tag v1.0.0 && git push origin v1.0.0
```

## Installing on the QNAP (once)

1. Create the folder `/share/Container/lanmon/` with a `data/` subfolder.
2. Copy `docker-compose.yml` and `.env` there (start from `.env.example`; fill in
   `LANMON_SECRET_KEY`).
3. Over SSH on the QNAP, sign in to GHCR with a Personal Access Token that has `read:packages`:
   ```bash
   docker login ghcr.io -u dvaduva
   ```
4. Start it:
   ```bash
   cd /share/Container/lanmon
   docker compose pull && docker compose up -d
   curl http://127.0.0.1:8765/health
   ```

## Network validation (phase 0)

From inside the container, with the IP and MAC of a PC that has WOL enabled (switched off for
the WOL test):

```bash
docker exec -it lanmon python -m app.tools.netcheck arp
docker exec -it lanmon python -m app.tools.netcheck ping 192.168.1.10
docker exec -it lanmon python -m app.tools.netcheck wol aa:bb:cc:dd:ee:ff --wait-ip 192.168.1.10
```

If the detected interface or subnet is wrong, add `--interface eth0` or
`--subnet 192.168.1.0/24` before the command (or set `LANMON_INTERFACE` / `LANMON_SUBNET`).

If the PC doesn't start: WOL enabled in the BIOS/UEFI, "Wake on Magic Packet" enabled in the
network adapter's driver, Fast Startup disabled in Windows, PC connected by cable. If the running
PC doesn't answer ping: enable the "File and Printer Sharing (Echo Request - ICMPv4-In)" firewall
rule for the Private profile.

## Updating

```bash
cd /share/Container/lanmon
docker compose pull && docker compose up -d
```

## First start

On first visit the app asks you to create the administrator account. If `LANMON_SECRET_KEY` is
missing, a key is generated in `secret.key` in the data folder (`/data` in the container), so
sessions survive restarts. With HTTPS (`tailscale serve`), set `LANMON_COOKIE_SECURE=true`.

## Settings

The **Settings** page changes the ping interval (default 30 s), the ARP scan interval (300 s),
the WOL check timeout (180 s) and the scanned subnet (empty = automatic detection; takes
precedence over `LANMON_SUBNET`). New intervals apply from the next cycle; a new subnet applies
after the container restarts. The page also holds the language, password change and backup.

Status history and the action log are kept for 90 days; the cleanup runs nightly at 03:30.

## Notifications

Settings → Notifications. Channels:

- **Telegram:** create a bot with @BotFather and copy its token. Send the bot a message, then open
  `https://api.telegram.org/bot<TOKEN>/getUpdates`; the number in `"chat":{"id":…}` is the
  Chat ID.
- **Email:** any SMTP server. For Gmail: `smtp.gmail.com`, port 587, STARTTLS and an
  [app password](https://myaccount.google.com/apppasswords).

The "Send a test message" button checks the setup right away. Rules: a device with "Notify me
when it's offline" ticked on its page stays offline longer than the threshold (and when it comes
back), a new unknown device appears on the network, a WOL wake-up fails. The token and password
are stored in the database in `/data`, so they're also in the backup.

## WOL schedules

Settings → WOL schedules (or the link on a device's Wake-on-LAN card): a device or a whole room,
at a time on chosen days. The time is in the `LANMON_TZ` time zone (default Europe/Bucharest).
Only devices with WOL that aren't already online are woken; a schedule missed because the app was
down still runs only if at most 10 minutes have passed. Scheduled wake-ups show in the Log as
"scheduled".

## TCP port checks

Besides ping, LanMon can check that a service answers: default ports per type (Rooms → Types,
e.g. `3389` for PCs, for Remote Desktop) or a device's own ports on its page (they take
precedence; `-` turns the check off). Ports are checked only while the device answers ping, on
the same interval. After two checks in a row with closed ports, "Service down" shows on the
Dashboard and the device page; devices with notifications also send a message.

## Uptime

The "Uptime" button on the Dashboard opens a page with the share of time each device was online
over the last 7 and 30 days, plus a bar per day (dark green = online all day, orange = less than
half, grey = no data). The device page shows the same percentages and the last 7 days. The
calculation uses the status history in the `LANMON_TZ` time zone; time before the first record
isn't counted.

## Remote shutdown

LanMon can shut a PC down over SSH with an ed25519 key generated on first start (the `ssh_key`
file in the data folder; keep it with your backups, otherwise it has to be added to the PCs
again).

1. Copy the public key from **Settings → Remote shutdown**.
2. Add it on the PC:
   - **Windows 10/11:** Settings → System → Optional features → *OpenSSH Server*, then in
     PowerShell as administrator: `Start-Service sshd; Set-Service sshd -StartupType Automatic`.
     For an administrator account the key goes in
     `C:\ProgramData\ssh\administrators_authorized_keys` (then
     `icacls C:\ProgramData\ssh\administrators_authorized_keys /inheritance:r /grant "Administrators:F" /grant "SYSTEM:F"`);
     for a regular account, in `C:\Users\<name>\.ssh\authorized_keys`. The command run is
     `shutdown /s /t 0`.
   - **Linux / macOS:** the key goes in `~/.ssh/authorized_keys` (on macOS, enable *Remote
     Login*). The command run is `sudo -n shutdown -h now`, so the user needs a passwordless sudo
     rule, e.g. in `/etc/sudoers.d/lanmon`: `dan ALL=(root) NOPASSWD: /sbin/shutdown, /usr/sbin/shutdown`.
3. On the device page, tick "Allow remote shutdown" and fill in the SSH user and the operating
   system.

The "Shut down" button shows on the device page while the device is online and asks for
confirmation. On the first connection LanMon remembers the PC's SSH host key; if it changes
(e.g. a reinstalled system), shutdowns are refused until you tick "Forget the remembered SSH host
key" on the device page. Every shutdown shows in the Log. Members can shut down devices in their
rooms.

## Users

Settings → Users (administrators only). An **administrator** sees and changes everything. A
**member** sees only the devices in the rooms ticked on their account, can open their pages and
wake them; they can't open Unassigned, Rooms, Log, Schedules or Settings (except their own
password and language). The account created on first start is an administrator, and at least one
administrator must always remain.

## Home Assistant (REST API)

Create a token in **Settings → Home Assistant (API)**; it's shown only once. Every request carries
the `Authorization: Bearer <token>` header:

| Method | Path | What it does |
| --- | --- | --- |
| GET | `/api/v1/devices` | the device list, with `online`, `status`, `ip`, `room`, `service_status` |
| GET | `/api/v1/devices/{id}` | one device (the id is in its page's address: `/devices/{id}`) |
| POST | `/api/v1/devices/{id}/wake` | Wake-on-LAN (409 if it isn't enabled) |
| POST | `/api/v1/devices/{id}/shutdown` | shutdown over SSH (409 if it isn't enabled) |

API messages are in English. Actions show in the Log with the source "Home Assistant". Example
for `configuration.yaml` (the token in `secrets.yaml` as `lanmon_auth: "Bearer lm_..."`):

```yaml
rest:
  - resource: http://qnap.local:8765/api/v1/devices/1
    headers:
      Authorization: !secret lanmon_auth
    scan_interval: 30
    binary_sensor:
      - name: Office PC
        device_class: power
        value_template: "{{ value_json.online }}"

rest_command:
  office_pc_wake:
    url: http://qnap.local:8765/api/v1/devices/1/wake
    method: post
    headers:
      Authorization: !secret lanmon_auth
  office_pc_shutdown:
    url: http://qnap.local:8765/api/v1/devices/1/shutdown
    method: post
    headers:
      Authorization: !secret lanmon_auth

template:
  - switch:
      - name: Office PC
        state: "{{ is_state('binary_sensor.office_pc', 'on') }}"
        turn_on:
          action: rest_command.office_pc_wake
        turn_off:
          action: rest_command.office_pc_shutdown
```

Replace `qnap.local:8765` with the address LanMon runs at (or its Tailscale address).

## Backup and restore

- **Manual:** Settings → "Download backup" saves a full copy `lanmon-<date>.db` (devices, rooms,
  types, history, log, accounts).
- **Automatic:** before every schema migration the app stores a copy in
  `/data/backups/pre-migration-*.db` (the last 5 are kept).

Restoring (also on a fresh install):

```bash
cd /share/Container/lanmon
docker compose down
cp lanmon-2026-10-25.db data/lanmon.db
rm -f data/lanmon.db-wal data/lanmon.db-shm
docker compose up -d
```

The accounts come from the backup. If `secret.key` (or `LANMON_SECRET_KEY`) differs, only open
sessions expire and you sign in again.

## Phone app (PWA)

Open LanMon in the browser and choose "Add to Home Screen" (Safari: the Share button; Chrome: the
⋮ menu → "Install app"). It opens full screen with its own icon. A full install (with the service
worker) needs HTTPS, i.e. `tailscale serve`; over `http://` the shortcut works, but without
offline static files.

## Remote access

Tailscale installed from the App Center on the QNAP and on the phone. Address:
`http://<qnap-name>:8765` through MagicDNS. After a QTS restart, check that Tailscale starts on
its own.
