# Live Observation & Shadow Operations v1 (LOSO v1)
## BUJJI Options OS — Engineering Sprint 104

**Status:** Design and integration-audit sprint. **No live session was
executed as part of this sprint** -- doing so requires real FYERS
credentials, real market hours, and an operator's explicit supervision,
none of which this environment can responsibly provide on its own
initiative. Every deliverable below is either (a) a real, verified
finding about EXISTING code, or (b) a design ready for an operator to
run. **No frozen decision module was touched. No broker order was
placed. No live connection was opened.**

---

## 1. Deliverable 1 — Live Data Audit

The single most important finding of this sprint: **far more live infrastructure already exists than the prior 103 sprints ever used.**

| Component | Real finding | Classification |
|---|---|---|
| `bujji/broker/fyers_ws.py::FyersTickFeed` | **Real, working FYERS WebSocket client** (wraps the official `fyers_apiv3.FyersWebsocket.data_ws.FyersDataSocket`, runs on a background thread). Exposes `start()`/`stop()`/`subscribe(symbols)`, a thread-safe poll-based `latest(symbol)` price store, `tick_age_seconds(symbol)`, `is_connected()`, `connect_count()`, `last_error()`, and `on_connect`/`on_disconnect` hooks. | **REUSABLE DIRECTLY.** This is the live spot/option-tick transport LOSO needs -- it already exists, already handles reconnects (`connect_count`), and was never touched by Series 77-103 (which are all replay-only). |
| `bujji/broker/base.py::Broker.live_tick_credentials()` | Real abstract hook `(app_id, access_token) | None`, deliberately gating WebSocket wiring to only where credentials concretely exist. | **REUSABLE DIRECTLY** -- exactly the seam a session controller should check before attempting a live connection. |
| `bujji/live_observation/` (Series 74) | **Real, already-documented (`docs/LIVE_OBSERVATION_PRODUCER.md`) tick→Observation translation engine.** `translate_event`, `new_window`/`add_tick`/`should_close`/`close_window` (candle aggregation from ticks), a `ProducerState` transition state machine (`is_valid_transition`/`apply_transition`), and its own journal. Its own founding philosophy, stated verbatim in its docs: *"Historical replay and live production produce identical Observation objects. Only the producer changes."* | **REUSABLE DIRECTLY, unmodified** -- this is precisely the "prove live behaves identically to replay" bridge Sprint 104 asks for, already built in Series 74 and never wired to the MSI arc since. |
| `bujji/live_market_events/` | Real, working: `detect_duplicate`, `detect_late_observation`, `detect_series_gap`, plus the same `detect_price_change`/`detect_oi_change`/`detect_vix_change`/`detect_futures_updated`/`detect_option_chain_updated` this whole arc's replay scripts already call continuously. | **REUSABLE DIRECTLY, already in continuous use** -- and it already implements Deliverable 4's "duplicate events"/"observation gaps" detection; nothing new needs to be built for that. |
| Spot feed / India VIX / futures feed | `FyersBroker` (REST) already exposes `get_spot`, `get_vix`, futures candles -- all real, all already used by prior live-only production code, never exercised by this MSI arc. | **REUSABLE, adapter required** -- these are synchronous-per-call REST methods, not a streaming feed; a session controller would poll them at a controlled cadence, not subscribe to them. |
| Broker reconnect logic | `ExecutionEngine._with_retry` (Series 98's own audit) plus `FyersTickFeed`'s own `on_disconnect` hook and `connect_count`. | **REUSABLE DIRECTLY**, already audited once in Series 98, re-confirmed here. |

**Conclusion**: Deliverable 1's own instruction ("do not rebuild feeds") is easy to honor -- there is nothing to rebuild. The real gap is INTEGRATION: no existing code connects `FyersTickFeed`/`live_observation`/`live_market_events`'s real live path to the MSI → Thesis → ... → Shadow Trading → Decision Auditor → Performance Analytics chain that Series 77-101 built entirely against replay data. That integration is this sprint's actual design deliverable (Section 2).

## 2. Deliverable 2 — Live Session Controller (design)

A thin orchestration layer -- **no new decision logic, only sequencing and wiring of entirely existing, frozen components**:

```
LiveSessionController.run_session():
    1. wait_for_market_open()          — poll real exchange hours; NEW, small, no live infra exists for this today (disclosed gap).
    2. feed = FyersTickFeed(...)        — REUSED DIRECTLY (Series 74/broker).
       feed.start(); feed.subscribe([...])
    3. On each real tick/candle close:
         observation = live_observation.engine.translate_event(tick_event)   — REUSED DIRECTLY.
         events = live_market_events.engine.detect_price_change(observation, previous)  — REUSED DIRECTLY.
         episodes = market_episode.engine.advance_time/process_event(...)    — REUSED DIRECTLY, unmodified.
    4. At the decision cadence (matching Sprint 103's 09:20 slot):
         thesis = msi_trade_thesis.engine.derive_trade_thesis(...)           — FROZEN, unmodified.
         ... (Expression -> Selection -> Construction -> Portfolio -> Margin -> Execution Planning -> Decision Auditor,
              every single one FROZEN and unmodified, exactly as Sprint 103's own pipeline already calls them)
    5. If TRADE_APPROVED: shadow = msi_shadow_trading.engine.open_shadow_position(...)  — FROZEN, unmodified. NEVER a broker order.
    6. Throughout the session: msi_position_lifecycle.engine.assess_position_lifecycle(...) — FROZEN, reused via Shadow Trading's own existing import, never called twice.
    7. finalise_session(): outcome = msi_decision_auditor.engine.build_outcome_record(...); archive everything (Sprint 103's own archive design).
```

**Never submits an order** -- structurally true, not merely promised: this controller design calls ONLY `open_shadow_position`/`track_shadow_position` (Series 100) for position lifecycle, never `bujji.execution.engine.ExecutionEngine.submit_and_confirm` or any `Broker.place_order` implementation. The SAME AST-level import bans this whole arc already enforces (`msi_shadow_trading`'s own test forbids importing `bujji.execution`/`bujji.broker`) apply unchanged to this controller's own future implementation.

## 3. Deliverable 3 — Live vs. Replay Consistency (design)

For any completed live session, the SAME real day's data (once captured) can be re-run through the existing replay scripts (Series 88-101's own established per-day pattern) and compared field-by-field:
- Decision records (`decision_id`, `thesis_type`, `strategy_family`, `decision_outcome`) must match exactly.
- Execution plans (`plan_id`, stage count, `estimated_orders`) must match exactly.
- Lifecycle states across the session must match exactly.
- Outcome records (`realised_movement_pct`, `realised_direction`) must match exactly, GIVEN the same real underlying market data was captured by both paths.

**Any divergence is recorded, never hidden or silently reconciled** -- a real divergence would mean either (a) live and replay captured genuinely different real market data (a data-capture issue, not a logic bug), or (b) a real, undiscovered non-determinism in a "frozen" module -- both are exactly the kind of finding this whole arc's discipline (Series 88's REGIME_STABLE investigation, Series 94's coverage investigation) already treats as valuable, not embarrassing.

## 4. Deliverable 4 — Operational Monitoring (design, reusing real detectors)

| Signal | Source |
|---|---|
| Data latency | `FyersTickFeed.tick_age_seconds(symbol)` -- REAL, already exists. |
| Missing ticks / observation gaps | `live_market_events.engine.detect_series_gap` -- REAL, already exists. |
| Reconnects | `FyersTickFeed.connect_count()` / `on_disconnect` hook -- REAL, already exists. |
| Session interruptions | `ProducerState`'s own transition state machine (`live_observation.engine`) -- REAL, already exists. |
| Duplicate events | `live_market_events.engine.detect_duplicate` -- REAL, already exists. |
| Replay divergence | Section 3's own comparison, NEW (this sprint's design), but built entirely from existing serialization functions (Series 99-101's own `*_to_dict` helpers). |

**Every monitoring signal except replay divergence itself already exists in working code** -- a genuinely surprising and valuable audit result for this sprint.

## 5. Deliverable 5 — Session Integrity Record (design)

One immutable record per session (mirrors this whole arc's own `frozen=True` dataclass convention):

```
LiveSessionRecord:
  session_id, date, status ("COMPLETED" | "ABORTED" | "RECOVERY_USED"),
  evidence_completeness ("COMPLETE" | "PARTIAL" | "NONE"),
  determinism_status ("MATCHED_REPLAY" | "DIVERGED" | "NOT_YET_COMPARED"),
  abort_reason (Optional[str]),
  recovery_used (bool), recovery_reason (Optional[str]),
  provenance, schema_version
```

## 6. Deliverable 6 — Daily Live Report (design, extends Sprint 103's template)

Identical to Sprint 103's Daily Research Report (Section 4 there), with two ADDITIONAL real fields once a live session actually runs: `replay_comparison` (Section 3's verdict) and `evidence_status` (Deliverable 5's `evidence_completeness`). No new report engine needed -- Sprint 103's own report generator already accepts arbitrary real per-day fields; this only adds two more.

## 7. Deliverable 7 — Readiness Tracking (design)

Sprint 102's own `EVIDENCE_COLLECTION_FRAMEWORK.md` Section 2 (Coverage Report) and Section 8 (Reliability Gates) gain two new real inputs once live sessions accumulate: `completed_live_sessions` (a new count, separate from replay-day count) and `replay_parity` (the real fraction of live sessions whose Section 3 comparison matched exactly). Gate A (100 completed shadow trades) is agnostic to whether a trade came from replay or a live session -- both count identically toward it, since Shadow Trading's own engine is unmodified and identical either way.

## 8. Deliverable 8 — Failure Discipline (design)

**Never attempt recovery by changing a decision** -- if a live session's evidence is incomplete (a feed drop, a gap `detect_series_gap` flags as severe, a reconnect that lost real ticks), the controller design in Section 2 aborts the SESSION, records the reason in the Section 5 integrity record, and preserves whatever partial evidence was real and already captured (Decision Records/Shadow Positions already built before the failure point are archived exactly as-is, per Sprint 103's own append-only archive discipline) -- it never fabricates the missing part of the session, and never silently falls back to a replay value to "fill in" a live gap.

## 9. Live data architecture

```
FyersTickFeed (real, WebSocket) ──► live_observation.engine.translate_event ──► Observation (SAME type as replay)
                                                                                      │
                                                                                      ▼
                                                              live_market_events.engine.detect_*  (SAME functions as replay)
                                                                                      │
                                                                                      ▼
                                                     market_episode.engine  →  MSI  →  ... (FROZEN, Series 77-101, unmodified)
```

## 10. Integrity guarantees

Every guarantee in this sprint is either (a) already structurally true because the underlying engines are pure functions (Series 90-101), or (b) enforced by the SAME AST-level import bans this whole arc already uses to keep replay-only packages from touching a broker. No new guarantee mechanism was invented; this sprint only extends where those existing guarantees are exercised, from replay data to live data.

## 11. Production transition

Once Gate A is met (Sprint 102) with a real mix of replay-day and live-session evidence, and Section 3's replay-parity check has run clean across a meaningful number of real live sessions, the transition to Paper Trading is the same one Sprint 103 already described: swap Shadow Trading's "no order" behavior for a real broker paper-trading call -- still no code change to any frozen decision module.

## 12. Deliverable 10 — Recommendation: **Continue live shadow operation**

Evidence-based, and consistent with this sprint's own explicit disclosure that no live session was actually run here:

1. **This sprint's real, audited finding is that almost the entire live path already exists** (`FyersTickFeed`, `live_observation`, `live_market_events` -- all real, working, previously built in Series 74 and never exercised by the MSI arc). The remaining work is genuinely small: wire these together per Section 2's design, under an operator's supervision during real market hours.
2. **Sprint 102's Gate A (100 shadow trades) remains the binding constraint**, unchanged -- live sessions accelerate reaching it (one real trading day at a time, same as replay), they do not substitute for volume.
3. **No live session has yet been run**, so Section 3's replay-parity claim is a design, not yet a measurement -- running even a handful of real live sessions and confirming they match replay byte-for-byte on the same captured data is the concrete, necessary next evidence this sprint's own design calls for.

**This is Continue live shadow operation** (i.e., an operator should now run Section 2's design during real market hours), **not** Begin Paper Trading or a limited-capital pilot -- those remain gated on both Gate A's volume AND a real, demonstrated replay-parity track record neither of which exists yet, since this sprint produced the design and the audit, not an executed live session.
