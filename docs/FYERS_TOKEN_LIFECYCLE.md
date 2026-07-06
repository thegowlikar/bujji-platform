# FYERS Authentication Lifecycle — Verified, and What It Means for Deployment

**Bottom line: fully automatic renewal is supported, within a ~15-day
window.** It is not "fully automatic forever" — an interactive login is
still required once to bootstrap a `refresh_token`, and again whenever that
refresh_token itself expires (~15 days). This document distinguishes exactly
what was **verified live**, what is **cited from external documentation**
(not independently reproducible in this session), and what remains an
**unavoidable manual step**.

## What was verified directly, and how

1. **The official `fyers-apiv3` SDK has no refresh-token support at all.**
   Confirmed by introspecting the installed package: `SessionModel`'s public
   methods are exactly `generate_authcode`, `generate_token`, `get_hash`,
   `set_token` — no refresh method. A full-text search for "refresh" across
   every file in the installed package returned zero matches. This is an SDK
   gap, not evidence the API itself lacks the capability.

2. **FYERS's real REST API does support a `refresh_token` grant.** Verified
   by POSTing a deliberately invalid refresh_token directly to
   `https://api-t1.fyers.in/api/v3/validate-refresh-token` and getting a
   genuine **application-level** rejection:
   ```json
   {"code": -501, "message": "Please provide valid refresh token", "s": "error"}
   ```
   This is not a network error or a generic gateway block (an earlier,
   naive test using Python's bare `urllib` got a Cloudflare bot-block
   — `error code: 1010` — for both this endpoint and the already-proven-working
   `/profile` endpoint; switching to `requests`, the library the SDK itself
   uses, made both work correctly). A `-501` "provide a valid token" response
   is only possible if the endpoint parsed the request and evaluated the
   refresh_token specifically — proving the endpoint is real, live, and
   enforces exactly the contract implemented in
   `bujji/broker/fyers_token_manager.py`.

3. **The `appIdHash` construction was read from the SDK source, not
   guessed**: `fyersModel.SessionModel.get_hash()` computes
   `sha256(f"{client_id}:{secret_key}")`. The token manager replicates this
   exactly (`FyersTokenManager._app_id_hash`), and a unit test asserts the
   two constructions match byte-for-byte.

## What is cited, not independently verified

- **Access token validity: ~24 hours (until end of trading day / next
  morning)**. Consistent with direct observation earlier in this project (a
  live token issued at one point had expired hours later, same day), but the
  exact boundary (fixed 24h vs. a specific daily cutover time) was not
  precisely pinned down.
- **Refresh token validity: ~15 days.** Sourced from FYERS community
  documentation and third-party developer writeups (see Sources below) —
  this project did not (and could not, in one session) wait 15 days to
  confirm the exact boundary. Treat "~15 days" as the planning assumption,
  not a guarantee; the failure mode if it's shorter is simply an earlier,
  clearly-labeled `AuthenticationError` telling the operator to re-login —
  never a silent failure.

## What remains an unavoidable manual step

**Obtaining the first `refresh_token` (and any new one after the ~15-day
window) requires the interactive login flow**: open a FYERS login URL in a
browser, authenticate with real login credentials + TOTP, and complete the
auth_code → access_token exchange. This is `fyers_token_refresh.py`
(`~/bujji-mcp/`), already built and working for a related project. FYERS
does not offer any programmatic, credential-only path to the *first* token —
correctly so; automating that would mean storing a login password/TOTP
secret, which is not something this project attempts, per the instruction to
recommend the safest approach rather than build unsupported workarounds.

**One thing to do before automatic renewal can activate**:
`fyers_token_refresh.py` currently only extracts and saves `access_token`
from its `generate_token()` response — it discards any `refresh_token` field
the response may contain. To bootstrap automatic renewal, that script (in
the sibling `bujji-mcp` project, not part of this repository, so not
modified here without being asked) needs one small addition: also
`set_key(..., "FYERS_REFRESH_TOKEN", response.get("refresh_token"))` if
present, and prompt for/store the account's **trading PIN** (a 4-digit code,
distinct from the login password and TOTP) as `FYERS_PIN`.

## Credential inventory and how BUJJI now uses them

| Env var | Purpose | Sensitivity |
|---|---|---|
| `FYERS_APP_ID` | Client/app identifier | Low — not a secret by itself |
| `FYERS_ACCESS_TOKEN` | Current session token (~24h) | High — usable to trade immediately |
| `FYERS_APP_SECRET` | Used only to compute `appIdHash` for refresh | High |
| `FYERS_REFRESH_TOKEN` | Exchanges for a new access_token, ~15 days | High — longer-lived than the access token itself |
| `FYERS_PIN` | Required by the refresh endpoint | **Highest** — a trading PIN, not just an API secret |
| `FYERS_CREDENTIALS_FILE` | Optional; where renewed tokens get written back | Not a secret (a path), but the file it points to must be protected exactly like `.env` |

All are read the same way `FYERS_APP_ID`/`FYERS_ACCESS_TOKEN` already were
(`AppConfig.load()`, env vars override config, never committed) — **none of
this is optional to configure**; if `FYERS_APP_SECRET`/`FYERS_REFRESH_TOKEN`/
`FYERS_PIN` are absent, `FyersBroker.connect()` behaves exactly as it always
has (fails fast with a clear `AuthenticationError` on an invalid token) — the
new subsystem only activates when fully configured, so there is zero
behavior change for anyone who doesn't opt in.

**Security recommendation**: store `FYERS_PIN` with at least the same
protection as `FYERS_ACCESS_TOKEN` (file permissions `600`, owned by the
service user, never logged — confirmed `FyersTokenManager` never logs the
PIN or refresh_token value itself, only a boolean "refreshed" event). If a
credentials file (`FYERS_CREDENTIALS_FILE`) is configured, ensure its
directory is writable by the service user and not world-readable.

## How the automatic renewal works (implemented this pass)

`FyersBroker.connect()`: on an `AuthenticationError` from the initial
session-validation call, if `FyersTokenManager.can_refresh` is true, it
calls `POST /validate-refresh-token`, updates the in-memory `access_token`,
rebuilds the SDK client against it, and retries the validation call once —
all before `connect()` would otherwise raise. If a `FYERS_CREDENTIALS_FILE`
is configured, the renewed `access_token`/`refresh_token` are written back
atomically (via `python-dotenv`'s `set_key`) so a process **restart** also
picks up the fresh token, not just the running process.

If refreshing isn't configured, or the refresh_token itself has expired,
the original clear, actionable `AuthenticationError` is raised unchanged —
exactly the pre-existing E1/E2 behavior. Nothing about strategy, risk, or
orchestration logic was touched; this is entirely inside
`bujji/broker/fyers.py` and the new `bujji/broker/fyers_token_manager.py`.

## Tests

`tests/test_fyers_token_manager.py` (10 tests) — hash construction verified
against the SDK's real formula, refresh success/failure against the
verified real response shapes, persistence to a credentials file, and
`FyersBroker.connect()`'s auto-refresh-then-retry integration (both the
"configured, succeeds" and "not configured, fails clearly unchanged" paths).
All mock `requests.post` — no live network dependency in the suite; the
contract itself was verified live, separately, before writing these tests.
156 tests passing total, no regressions.

**Not performed in this session, and why**: a full live round-trip using a
genuine `refresh_token` was not attempted. Doing so requires first
completing one interactive login to mint a real refresh_token, and
providing the account's trading PIN — not something to solicit or handle by
pasting into a terminal/chat session. The endpoint's *contract* is verified
live (an invalid-token rejection is exactly what a genuine call would also
receive if the token were bad); the *success* path is verified by
unit test against that same confirmed contract, not by a live success call.

## Linux deployment (`deploy/bujji.service`)

Updated (`deploy/README.md`) to document the three new optional env vars.
The unit file's `EnvironmentFile=` directive already covers this — no
change needed to the unit file itself, since it was already designed to
source all FYERS credentials from one file. The operational recommendation:
run `fyers_token_refresh.py` (once modified to also capture
`refresh_token`, per above) once to bootstrap, then the running service
renews itself daily without intervention for ~15 days, at which point
`journalctl -u bujji` will show a clear `AuthenticationError` asking for a
fresh interactive login — the same actionable failure mode that existed
before this subsystem, just now only once every ~15 days instead of daily.

## Sources

- [Fyers-API-Access-Token-Generation-V2](https://github.com/tkanhe/Fyers-API-Access-Token-Generation-V2) —
  community tooling confirming the interactive auth_code flow.
- [fyers-api-access-token-v3](https://github.com/tkanhe/fyers-api-access-token-v3) —
  community reference for the v3 flow.
- FYERS community forum threads on refresh-token usage and expiry
  (referenced via search; the ~15-day figure and the `pin`-based
  `validate-refresh-token` contract both trace to this and were then
  independently confirmed live against the real endpoint, per above).
- Official docs entry point: `https://myapi.fyers.in/docs/` (dashboard:
  `https://myapi.fyers.in/dashboard/`) — not fetchable directly in this
  session (returned 404 for the specific sub-path tried); the SDK source and
  the live endpoint test are the primary evidence this document relies on.
