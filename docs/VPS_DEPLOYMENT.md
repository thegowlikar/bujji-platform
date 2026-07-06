# BUJJI — Ubuntu LTS VPS Deployment Guide

Complete, copy-pasteable procedure to deploy BUJJI on a clean Ubuntu 22.04 or
24.04 LTS server, running in `fyers_paper` mode (live FYERS market data,
paper-only execution — no real orders). Linux is the intended long-term
runtime; macOS was only ever a development host.

Conventions used below: service user `bujji`, install root `/opt/bujji`. Adjust
if you prefer, but keep them consistent with `deploy/bujji.service`'s
`ReadWritePaths`.

---

## 0. Prerequisites

- A clean Ubuntu 22.04 / 24.04 LTS VPS with sudo access.
- Set the server timezone to IST so `journalctl` timestamps read naturally
  (the app itself is timezone-correct regardless — it pins Asia/Kolkata
  internally via `bujji/core/clock.py`, D1/D2 — but host TZ still affects log
  readability and cron):
  ```bash
  sudo timedatectl set-timezone Asia/Kolkata
  timedatectl   # verify; confirm NTP is active ("System clock synchronized: yes")
  ```
- Your FYERS API app credentials (App ID, App Secret) from
  https://myapi.fyers.in/dashboard/ , and your account's 4-digit trading PIN.

## 1. System packages & the service user

```bash
sudo apt update && sudo apt install -y python3 python3-venv python3-pip git
python3 --version   # 3.10 (22.04) or 3.12 (24.04) — both supported

# Dedicated, unprivileged service user with no login shell.
sudo useradd --system --create-home --home-dir /opt/bujji --shell /usr/sbin/nologin bujji
```

## 2. Clone the code

```bash
sudo -u bujji git clone https://github.com/thegowlikar/bujji-platform.git /opt/bujji/app
# Everything below assumes the repo lives at /opt/bujji/app.
```

## 3. Virtual environment & dependencies

```bash
sudo -u bujji python3 -m venv /opt/bujji/.venv
sudo -u bujji /opt/bujji/.venv/bin/pip install --upgrade pip
sudo -u bujji /opt/bujji/.venv/bin/pip install -r /opt/bujji/app/requirements.txt

# Smoke test: the suite must pass before you trust the install.
sudo -u bujji bash -c 'cd /opt/bujji/app && /opt/bujji/.venv/bin/python -m pytest -q'
```

## 4. Runtime directories & permissions

The app writes logs, the SQLite journal, the session-recovery snapshot, and the
single-instance lock under its working directory. The systemd unit's
`ReadWritePaths` grants write access to exactly these and nothing else.

```bash
sudo -u bujji mkdir -p /opt/bujji/app/logs /opt/bujji/app/data
sudo chmod 700 /opt/bujji/app/data      # journal/snapshot are private
sudo chmod 750 /opt/bujji/app/logs
```

## 5. Credentials file (the ONLY place secrets live)

Create `/opt/bujji/.env`, owned by `bujji`, mode `600`. **Never** commit this,
never put secrets in `config.yaml` or the unit file.

```bash
sudo -u bujji tee /opt/bujji/.env >/dev/null <<'EOF'
# --- required ---
FYERS_APP_ID=XXXXXXXXXX-100
FYERS_ACCESS_TOKEN=paste-after-interactive-login
# --- optional: enables automatic ~15-day token auto-renewal ---
FYERS_APP_SECRET=your-app-secret
FYERS_REFRESH_TOKEN=paste-after-interactive-login
FYERS_PIN=1234
# Write renewed tokens back to THIS same file so restarts pick them up:
FYERS_CREDENTIALS_FILE=/opt/bujji/.env
EOF
sudo chown bujji:bujji /opt/bujji/.env
sudo chmod 600 /opt/bujji/.env
```

`FYERS_ACCESS_TOKEN` / `FYERS_REFRESH_TOKEN` are obtained via the one-time
interactive browser login — see **Token management** below. Everything except
`FYERS_APP_ID`/`FYERS_ACCESS_TOKEN` is optional; without the optional set, the
bot still runs but requires a manual token refresh daily instead of every
~15 days.

## 6. Point `config.yaml` at the right paths

The repo default `config/config.yaml` uses relative paths (`logs`, `data/…`),
which resolve correctly because the unit sets `WorkingDirectory=/opt/bujji/app`.
Confirm `broker.name: fyers_paper` (the shipped default). No other change is
required for a paper campaign.

## 7. Install & start the systemd service

```bash
# The unit ships pointing at /opt/bujji; retarget it to the /opt/bujji/app clone.
sudo cp /opt/bujji/app/deploy/bujji.service /etc/systemd/system/bujji.service
sudo sed -i 's#/opt/bujji/.venv#/opt/bujji/.venv#; s#WorkingDirectory=/opt/bujji#WorkingDirectory=/opt/bujji/app#; s#--config /opt/bujji/config#--config /opt/bujji/app/config#; s#ReadWritePaths=/opt/bujji/logs /opt/bujji/data /opt/bujji/.env#ReadWritePaths=/opt/bujji/app/logs /opt/bujji/app/data /opt/bujji/.env#' /etc/systemd/system/bujji.service

# Sanity-check the unit before enabling it.
sudo systemd-analyze verify /etc/systemd/system/bujji.service

sudo systemctl daemon-reload
sudo systemctl enable --now bujji.service
```

> Review the edited unit by hand (`sudoedit /etc/systemd/system/bujji.service`)
> if the `sed` above doesn't match your chosen paths — the four things that must
> be right are `ExecStart`'s venv+config paths, `WorkingDirectory`,
> `EnvironmentFile`, and `ReadWritePaths`.

## 8. Log locations

- **systemd journal** (primary): `journalctl -u bujji -f` — all stdout/stderr,
  tagged `bujji`.
- **Structured JSONL app logs**: `/opt/bujji/app/logs/bujji_<YYYY-MM-DD>.jsonl`
  — one file per day, machine-parseable (every state transition, decision,
  VWAP audit, order event). Rotate/ship these as you would any app log; the app
  itself does not currently rotate them (a known limitation, below).
- **Dashboard** (read-only): `http://127.0.0.1:8787` on the VPS. It binds to
  localhost only — reach it via an SSH tunnel: `ssh -L 8787:127.0.0.1:8787 user@vps`.

## 9. Restart policy (already in the unit)

`Restart=always`, `RestartSec=5`, with `StartLimitIntervalSec=600` /
`StartLimitBurst=10` (rate-limits a crash loop to 10 starts / 10 min, then
stops — clear with `systemctl reset-failed bujji`). On reboot the unit
auto-starts (`WantedBy=multi-user.target`) and BUJJI's crash-recovery (C1) then
resumes or safely flattens any open position, and re-arms tick monitoring.

## 10. Token management

FYERS access tokens last ~24h; refresh tokens last ~15 days (see
`docs/FYERS_TOKEN_LIFECYCLE.md`). There is **no** credential-only way to mint
the *first* token — it needs one interactive browser+TOTP login.

**Bootstrap (once, and again roughly every ~15 days):** run the interactive
login on a machine with a browser (it does not have to be the VPS), using the
`~/bujji-mcp/fyers_token_refresh.py` flow, which now saves both the access and
refresh tokens. Copy the resulting `FYERS_ACCESS_TOKEN` and
`FYERS_REFRESH_TOKEN` into `/opt/bujji/.env` on the VPS, then
`sudo systemctl restart bujji`.

**Daily (automatic, unattended):** with `FYERS_APP_SECRET` / `FYERS_REFRESH_TOKEN`
/ `FYERS_PIN` / `FYERS_CREDENTIALS_FILE` all set, BUJJI renews the access token
itself on connect and writes both tokens back to `/opt/bujji/.env` atomically —
no action needed. Watch for `fyers_token_refreshed_automatically` in the logs.

**When the refresh token expires (~15 days):** `journalctl -u bujji` shows an
`AuthenticationError` telling you to re-login. Re-run the bootstrap.

## 11. Updates

```bash
sudo systemctl stop bujji
sudo -u bujji git -C /opt/bujji/app pull
sudo -u bujji /opt/bujji/.venv/bin/pip install -r /opt/bujji/app/requirements.txt
sudo -u bujji bash -c 'cd /opt/bujji/app && /opt/bujji/.venv/bin/python -m pytest -q'
sudo systemctl start bujji
```
Do updates outside market hours. The single-instance lock (F4) prevents the old
and new processes from ever overlapping.

## 12. Backups

Back up (they contain your trading record, not secrets):
- `/opt/bujji/app/data/trade_journal.csv` and `/opt/bujji/app/data/bujji.db`
  — the trade journal (CSV + SQLite).
- `/opt/bujji/app/logs/*.jsonl` — the audit trail.

Do **not** back up `/opt/bujji/.env` into any shared/unencrypted location — it
holds live credentials. If you must, encrypt it.

```bash
# Example: nightly journal backup (as the bujji user's crontab).
0 22 * * 1-5 tar czf /opt/bujji/backups/journal-$(date +\%F).tgz -C /opt/bujji/app data/trade_journal.csv data/bujji.db
```

## 13. Operational checks

```bash
systemctl status bujji                       # running? enabled?
journalctl -u bujji -n 50 --no-pager         # recent activity
journalctl -u bujji | grep -E "auth|error|EXIT|recovery"   # incidents
ls -l /opt/bujji/.env                        # must be -rw------- bujji bujji
# Dashboard (via SSH tunnel) — confirm WS Health "connected", tick age small.
```

**Green-light checklist for a live session (run before 09:15 IST):**
- `systemctl is-active bujji` → `active`
- Logs show `Bujji started | broker=fyers_paper` and no `auth_expired`
- Banner in logs shows `Live Orders: DISABLED ✓`
- Dashboard reachable; `clock_trusted` true; VWAP "REAL VOLUME" once candles flow
