# BUJJI — Production Credential Audit

Audit of how BUJJI handles FYERS secrets (App ID, App Secret, access token,
refresh token, trading PIN). Each item states the check, the evidence, and the
result. Two real issues were found and fixed during this audit (both noted).

## 1. No hardcoded secrets — PASS

- Searched all of `bujji/` for assigned secret literals
  (`access_token|refresh_token|app_secret|secret_key|FYERS_PIN = "…"`): zero
  matches.
- All credentials are read from environment variables in exactly one place
  (`AppConfig.load()`, `bujji/core/config.py`): `FYERS_APP_ID`,
  `FYERS_ACCESS_TOKEN`, `FYERS_APP_SECRET`, `FYERS_REFRESH_TOKEN`, `FYERS_PIN`,
  `FYERS_CREDENTIALS_FILE`. Config defaults are all `None`.
- `config.yaml` contains no secrets (only the non-sensitive `broker.name` and
  tuning knobs).

## 2. No secrets in logs — PASS

- The token manager logs only event names, never values:
  `fyers_token_refreshed_automatically` (success),
  `fyers_token_refresh_not_persisted` (a config warning). The PIN, secret, and
  token *values* are never passed to a logger.
- `FyersBroker.connect()`'s one token-related log line,
  `fyers_access_token_invalid_attempting_refresh: %s`, formats the caught
  `AuthenticationError` — whose message is constructed from FYERS response
  `code`/`message` fields only, never the token/secret/PIN. Verified by reading
  every `raise AuthenticationError` site: none embed a credential value.
- The interactive bootstrap script (`~/bujji-mcp/fyers_token_refresh.py`) prints
  only "saved" confirmations, never the token/secret/PIN values (verified).

## 3. Tokens never committed to Git — PASS

- Platform repo `.gitignore` excludes `data/` and `.env`.
- `git ls-files` shows zero actual credential/data files tracked (the only hits
  for "token"/"credential"/"session_state" are *source file names*, not data).
- Sibling `~/bujji-mcp` repo: `.env` is gitignored (line 5) and confirmed
  untracked.

## 4. Refreshed tokens written atomically — FIXED, now PASS

- **Issue found:** the first implementation used python-dotenv's `set_key`.
  Reading its source (`dotenv.main.rewrite`): it writes to a
  `NamedTemporaryFile` in the *system* temp dir, then `shutil.move`. That move
  is only atomic when temp and target share a filesystem — but the production
  systemd unit sets `PrivateTmp=true`, making `/tmp` a separate tmpfs, so the
  move degrades to a non-atomic copy. Worse, `set_key` was called once per key,
  so a crash between the two calls could persist a new access token against a
  stale refresh token.
- **Fix:** `FyersTokenManager._persist` now updates both keys in memory, writes
  to a temp file **in the same directory** as the target (guaranteeing a
  same-filesystem, atomic `os.replace`), `fsync`s, and replaces in a single
  operation — the same proven pattern used by `SessionStore.save()`. Other keys
  and comments in the file are preserved.
- **Tested:** `test_persist_is_atomic_preserves_other_keys_and_sets_0600`
  verifies single-replace update, preservation of other keys/comments, no
  leftover `.tmp` file, and `0600` result mode.

## 5. Credential file permissions — FIXED, now PASS

- **Issue found:** the sibling `~/bujji-mcp/.env` was `-rw-r--r--` (644,
  world-readable). Hardened to `600` immediately (`chmod 600`), and the
  bootstrap script now enforces `0600` after every write.
- The atomic writer (`_persist`) creates its temp file with mode `0600` via
  `os.open(..., 0o600)` and re-`chmod`s before replace, so a refreshed
  credentials file is never even briefly world-readable.
- The VPS deployment guide mandates `chmod 600 /opt/bujji/.env`, owned by the
  `bujji` service user, and the systemd unit runs as that unprivileged user
  with `ProtectHome`/`ProtectSystem=strict` and write access limited to the
  data/logs dirs and the `.env` file only.

## 6. Credential rotation process — documented

**Access token (auto, daily):** on an auth failure at connect, if the refresh
credentials are configured, BUJJI exchanges the refresh token for a new access
token, updates memory, and atomically writes both back to
`FYERS_CREDENTIALS_FILE`. No human action; visible as
`fyers_token_refreshed_automatically`.

**Refresh token (manual, ~every 15 days):** when the refresh token expires, the
next connect raises a clear `AuthenticationError`. Re-run the interactive
bootstrap (`fyers_token_refresh.py`) to mint a new access+refresh pair, paste
both into `/opt/bujji/.env`, and `systemctl restart bujji`. See
`docs/FYERS_TOKEN_LIFECYCLE.md`.

**On suspected compromise:** revoke the app in the FYERS API dashboard (this
invalidates the tokens immediately), regenerate the App Secret, update
`/opt/bujji/.env`, re-run the interactive login, and restart. Because
`fyers_paper` mode cannot place real orders, exposure of a paper-mode token is
market-data-read + paper-ledger only — but the *token itself* is a full-account
credential, so treat it as high-sensitivity regardless of mode.

## Residual notes

- The trading **PIN** is the highest-sensitivity item (it authorizes the
  refresh grant). It lives only in `/opt/bujji/.env` (0600) and is never logged.
  An operator who does not want the PIN on the server can simply omit the
  optional refresh vars and accept manual daily token refresh instead.
- No secret is ever passed on a command line (which would expose it in the
  process table) — all come from the `EnvironmentFile`.
