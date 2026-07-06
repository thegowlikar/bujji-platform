# Process supervision (F2)

This directory contains the systemd unit that makes the bot auto-restart after
a crash or VPS reboot. Without this, Tier 1's crash-recovery (C1) and
single-instance lock (F4) guarantees only matter if the process is actually
running to exercise them — a crashed bot that never restarts still abandons
its position exactly like the original audit finding described.

## Install

```bash
sudo cp deploy/bujji.service /etc/systemd/system/bujji.service
sudo nano /etc/systemd/system/bujji.service   # edit User/WorkingDirectory/paths
sudo systemctl daemon-reload
sudo systemctl enable --now bujji.service
```

## Operate

```bash
systemctl status bujji          # current state
journalctl -u bujji -f          # follow logs
sudo systemctl restart bujji    # manual restart (e.g. after a token refresh)
sudo systemctl stop bujji       # graceful stop (SIGTERM -> releases F4 lock)
```

## What this does and does not guarantee

- **Does:** restart the process on crash, unhandled exception that escapes
  `Application.run()`, or `kill`. Restarts automatically after a VPS reboot
  (`WantedBy=multi-user.target` + `systemctl enable`).
- **Does:** automatically renew the FYERS access token daily, for up to ~15
  days, **if** `FYERS_APP_SECRET`/`FYERS_REFRESH_TOKEN`/`FYERS_PIN` are
  configured in the `EnvironmentFile` — see
  [docs/FYERS_TOKEN_LIFECYCLE.md](../docs/FYERS_TOKEN_LIFECYCLE.md) for the
  full verified lifecycle. Without those three, or once the refresh_token
  itself expires (~15 days), token expiry is still detection + fail-fast
  alerting only (E1/E2): the process restarts, fails auth again at startup,
  and keeps restarting on `RestartSec=5` until a human completes the
  interactive login again. Watch `journalctl -u bujji` for
  `startup_blocked_auth_failure` (not configured / refresh_token expired) vs.
  `fyers_token_refreshed_automatically` (working as intended).
- **Does not:** fix the underlying cause of a crash, or bootstrap the
  *first* `refresh_token` — that always requires one interactive login
  (browser + TOTP), by design; FYERS provides no credential-only path to it.
- **Does not:** protect against two supervised instances racing — F4's
  `ProcessLock` still enforces that independently; a `Restart=always` loop
  landing on a *still-running* prior instance (e.g. a slow shutdown) will
  correctly fail the new instance's lock acquisition and retry on the next
  `RestartSec` interval rather than run concurrently.

## Manual chaos drill (recommended before going live)

Per the chaos plan (F2), this cannot be fully validated by an automated test —
verify it operationally once, in paper-trading mode, before relying on it:

1. Start the service with a paper-trading config, let it open a position.
2. `sudo reboot` the VPS.
3. After it comes back, confirm `systemctl status bujji` shows it running and
   `journalctl -u bujji` shows a `recovery_resumed` (or `recovery_orphan_position` /
   `position_already_closed`) log line — i.e., C1 fired and the position was
   not abandoned.
