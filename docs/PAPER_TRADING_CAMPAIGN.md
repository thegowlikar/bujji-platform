# Extended Paper-Trading Campaign

The platform is frozen as of the F2/D1-D2/C_D1 pass (see
[CHAOS_TESTING_PLAN.md](CHAOS_TESTING_PLAN.md)'s priority list). No further
hardening should be implemented speculatively — the point of this campaign is
to let real operational evidence, not further guesswork, decide what (if
anything) from the remaining gap list actually needs attention before real
capital is committed.

## Before starting

1. `broker.name: fyers_paper` is now the **shipped default** in
   `config/config.yaml` — no change needed unless you've edited it locally.
   Confirm it still says `fyers_paper`, **not** plain `paper`. Plain `paper`
   is a fully synthetic simulator (random-walk spot and premiums); it
   validates the software, not market behaviour. `fyers_paper` runs against
   real FYERS market data with execution routed exclusively through the
   paper ledger — see [PAPER_TRADING_LIVE_DATA.md](PAPER_TRADING_LIVE_DATA.md)
   for the architecture and the safety guarantee that no real order can be
   placed in this mode. This is what "run the bot every market day using live
   FYERS data + paper execution" means in config terms.
   `FYERS_APP_ID`/`FYERS_ACCESS_TOKEN` must be set as environment variables —
   startup fails fast with a clear `AuthenticationError` naming the missing
   variable(s) if they aren't, before any trading logic runs. The startup
   banner (printed immediately, before anything else can fail) always shows
   the active mode, market-data source, execution destination, and a
   prominent `Live Orders: DISABLED ✓` / `ENABLED — REAL CAPITAL AT RISK`
   line — check it every time you start the process.
2. Install process supervision per [deploy/README.md](../deploy/README.md) —
   run the campaign under systemd, not a bare foreground process, so F2's
   crash-recovery guarantees are actually exercised end-to-end, not assumed.
3. Confirm the VPS (or wherever this runs) has NTP syncing — `ClockGuard`
   detects *in-session* drift, but a clock that's already wrong at startup is
   still a pre-flight checklist item, not something the code catches (see
   D1's documented scope limit).

## What to watch for during the campaign

Everything below is now visible on the dashboard's status object
(`RuntimeStatus`) and in the structured JSON logs — the campaign's job is to
observe whether any of these ever actually fire, and how:

- `duplicate_candles_ignored`, `last_candle_gap_seconds` — is the candle feed
  actually clean in practice, or does C_D1's detection fire regularly? If it
  fires often, that's evidence the underlying feed/scheduling needs attention
  — not evidence the detection code is wrong.
- `clock_trusted` / `clock_drift_detail` — did the VPS clock ever actually
  jump during a session? If never, D1's scope (in-session detection only, no
  external trusted-source validation) was probably sufficient. If it does fire
  and matters, that's the evidence needed to justify the deferred external
  validation.
- `auth_expired` — exercise a real token refresh at least once during the
  campaign (paper trading is the safe place to do this) and confirm the
  documented flow: detection → fail-fast → `journalctl`/logs show
  `startup_blocked_auth_failure` or `auth_expired_*` distinctly → manual
  refresh → restart → C1 resumes correctly.
- A real VPS reboot at least once (see the manual chaos drill in
  [deploy/README.md](../deploy/README.md)) with a position open — confirm
  `recovery_resumed` (or the orphan/already-flat variants) actually appears in
  `journalctl -u bujji`.
- Trade journal (`data/trade_journal.csv` / `bujji.db`) accuracy — do the
  thesis narratives, MFE/MAE, and exit reasons read as expected across many
  real (paper) trades, not just the handful exercised in unit tests?

## What NOT to do during the campaign

- Do not add new hardening speculatively because "it seems like a good idea."
  The explicit instruction behind this freeze was: remaining items (log
  rotation, config cross-field validation, margin pre-check, and the narrower
  edge cases in the chaos plan) are addressed **based on observed evidence**
  from this campaign, not spec-driven guessing.
- Do not modify strategy/entry/exit logic based on paper P&L during this
  campaign — the campaign's purpose is operational reliability evidence, not
  strategy tuning. Strategy changes are explicitly out of scope for all of
  this hardening work and remain a separate decision.

## Exit criteria (suggested, not automated)

A reasonable bar before considering live capital: multiple weeks of paper
trading spanning at least one VPS reboot, one manual token refresh, and no
unexplained `healthy=False` states that weren't already understood and
explained by the mechanisms above.
