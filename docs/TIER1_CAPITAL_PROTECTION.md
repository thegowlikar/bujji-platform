# Tier 1 — Capital-Protection Fixes (C1–C4)

These changes address the four **critical** findings from the production-readiness
audit that could cause capital loss. They touch **only** execution safety,
recovery, and reconciliation. **No entry or exit rule was changed** — the
strategy behaves identically; it is now merely impossible to abandon, duplicate,
mis-size, or strand a position.

Status: **implemented and verified** (40 tests passing, incl. 12 new Tier 1 tests
and 2 replay tests). Awaiting review before Tier 2.

---

## C1 — Crash recovery never abandons an open position

**Before:** `StateMachine` always started in `WAITING`; `_recover()` only logged.
After a restart while holding a short option, the bot did nothing (or, if the
day's single trade was already taken, went straight to `DONE_FOR_DAY`) — leaving
a live position unmanaged to expiry.

**After:** on startup the orchestrator reconciles the persisted snapshot against
live broker positions and takes the safe action for every case:

| Snapshot | Broker | Action |
|---|---|---|
| position | matching live short | **Resume** management (`restore` → `IN_POSITION`/`EXITING`), trusting the broker's actual quantity |
| position | flat | Position already closed → finalize to `DONE_FOR_DAY` |
| none | live short | **Orphan** → flatten immediately, flag unhealthy |
| none | flat | Clean start; only the trade count is carried forward |

Supporting changes:
- `core/position_codec.py` — full, lossless `Position` ⇄ dict serialization
  (contract, opening range, thesis).
- `core/session_state.py` — snapshot now stores the **full** position plus the
  entry/exit idempotency keys, and is written **atomically** (temp + `fsync` +
  `os.replace`) so a crash mid-write can't corrupt recovery.
- `core/state_machine.py` — `restore(state)` reinstates a prior state during
  recovery (audited via a `state_restored` log event), bypassing forward-only
  transition guards *by design*.

**Known, intentional limitation (not a Tier 1 capital risk):** after a restart
with *no* open position, the Signal Engine's intraday ORB/VWAP are not rebuilt,
so the bot will not open a *new* trade that day. This is a missed-opportunity
trade-off, not a loss risk, and is deferred to Tier 2.

## C2 — Guaranteed end-of-day square-off

**Before:** the run loop simply stopped after `trading_end`; a failing exit left
an overnight naked short. The 15:15 hard-exit candle could also never be
processed (FYERS candle timestamps are bar-open time).

**After:**
- `Orchestrator.square_off(reason)` / `end_of_day()` force-flatten any open
  position **without consulting strategy rules** — pure risk reduction.
- `app.py` runs EOD enforcement on the **wall clock**, independent of candle
  arrival: at/after `hard_exit` it calls `end_of_day()` every boundary and keeps
  retrying until flat; only then does the loop end. A stalled or failed feed can
  no longer strand a position.

## C3 — Order idempotency (no duplicate orders)

**Before:** `submit_and_confirm` wrapped `place_order` in a blind retry. A
timeout *after* the exchange accepted the order caused a duplicate order (double
size / wrong direction). The real FYERS API does not de-duplicate on our client
id.

**After (`execution/engine.py`):**
- The order is placed **at most once** per `client_order_id`. Before placing, we
  query the broker for that id and adopt an existing order if present (covers
  restart and retry).
- If `place_order` raises, we **do not re-place blindly** — we query whether it
  landed and only re-place when it is *confirmed absent* (`_place_idempotent`).
- The entry/exit client-order-ids are persisted to the snapshot **before**
  placement, so a crash between broker acceptance and our bookkeeping is
  recoverable.
- The FYERS adapter must send `client_order_id` as the broker `orderTag` and
  look it up in `get_order` (documented at the adapter seam).

## C4 — Partial fills

**Before:** only a full `FILLED` was treated as success. A partial fill was
cancelled and raised as an error while the broker still held the partial
position; the bot believed it was flat. Positions were also sized off the
*requested* quantity, not the filled quantity.

**After:**
- `submit_and_confirm` returns a truthful `filled_quantity`; a partial fill is
  **returned, not raised** (only a *zero* fill raises). Remaining working
  quantity is cancelled so it can't fill late.
- Entry sizes the `Position` off the **actual** filled quantity.
- Exit (`_do_exit`) **flattens fully**, re-submitting the residual until the
  position is genuinely flat; if it cannot, it stays in `EXITING`, flags
  unhealthy, and retries — it never falsely reports flat.

---

## Tests

- `tests/test_tier1_capital_protection.py` — codec round-trip; C1 resume /
  already-flat / orphan; C2 square-off (with & without a position); C3
  error-after-accept & duplicate-submit; C4 entry sizing & full-flatten through
  partials.
- `tests/test_tier1_replay.py` — normal exit still works through the refactor;
  C2 square-off of a position left open at end of replay.

Deterministic fault injection is provided by the `PaperBroker` hooks
(`partial_fill_qty`, `raise_on_place_after_record`, `seed_position`,
`place_calls`), all benign by default.

## Explicitly out of scope (deferred to Tier 2)

Timezone/clock correctness (C8), `daily_loss_limit` enforcement (C6), replay
path isolation (C7), CONFIRMED/EXITING wedge hardening (C5), candle de-dup (C10),
margin/product checks (C9), and all "Important"/"Nice-to-have" items remain
unaddressed by design.
