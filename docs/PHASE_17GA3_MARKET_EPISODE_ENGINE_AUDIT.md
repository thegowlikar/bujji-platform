# Phase 17G.A (continued) — `market_episode/engine.py` Full Read

**Status: AUDIT ONLY. No code.** Closes the one open item from the
prior audit pass. Full read complete, including `config.py`.

---

## 1. Design quality: strong, replay-safe by construction

`process_event()`/`advance_time()` are pure functions — no wall-clock
read anywhere in the module (`current_time` is always an explicit
caller-supplied string, same discipline as every store in this
engagement: `RawObservationStore`, `HistoricalObservationStore`, etc.).
Nine explicit invariants (INV-1 through INV-9), each backed by a named
test in `tests/test_market_episode_engine.py`. `episode_id` is a
deterministic content hash over `(episode_type, founding_event_id)` —
same minting discipline as `observation_id` elsewhere in this project.
Idempotent (INV-7), deterministic under replay (INV-5's explicit
tie-break rule), never merges episodes retroactively (INV-2), never
reopens a closed episode (INV-4). **This module was already built to
the same standard this engagement has held Reality/Memory-tier code to
— nothing here needs hardening before reuse.**

## 2. INV-8 — a real, concrete constraint for any historical bridge

`MarketEvent` doesn't uniformly carry `instrument` in `detail`, so
"same instrument" is enforced **structurally**, not by a field check:
one `process_event`/`advance_time` call sequence must process exactly
one instrument's event stream at a time. A historical bridge feeding
this engine from `HistoricalObservationStore` (spot + futures + VIX)
**must run three separate sequences**, one per instrument, never an
interleaved multi-instrument stream — confirmed as a hard, structural
requirement, not a style preference.

## 3. The proximity/silence windows — the key finding, now precise

```
DEFAULT_PROXIMITY_WINDOW_SECONDS = 300.0    (5 minutes)
DEFAULT_QUIESCENT_AFTER_SECONDS  = 900.0    (15 minutes)
DEFAULT_CLOSE_AFTER_SECONDS      = 1800.0   (30 minutes)
```

These are real, disclosed, live-tick-era defaults. Checked against what
now exists (unavailable when this engine was built):

| Reality resolution | Timestamp spacing | Fit against defaults |
|---|---|---|
| **5-minute intraday** (17H.9, 2017/2018→today) | Exactly 300 seconds between consecutive bars | **Drop-in fit.** `DEFAULT_PROXIMITY_WINDOW_SECONDS = 300.0` exactly matches consecutive-bar spacing — two adjacent 5-min `HistoricalObservation`-derived events would compatibility-check as adjacent with ZERO parameter changes. |
| **Daily** (17H.4/17H.6, 1998/2008/2018→today) | 86,400 seconds (or more, across weekends/holidays) between consecutive bars | **Does not fit as-is.** Every daily-derived event would exceed all three defaults by orders of magnitude — every bar would start a new, immediately-CLOSED episode, never grouping into anything. A daily-resolution bridge needs an explicit, deliberately-chosen window (not a default), e.g. a window sized in trading days rather than seconds, or accepting that daily-resolution "episodes" are structurally closer to single-bar events than the intraday grouping this engine was designed for. |

**This sharpens the prior audit's finding materially**: a 5-minute
historical bridge is a genuinely drop-in reuse of this engine with its
existing defaults; a daily bridge is not, and would need its own
explicit parameter decision (or a different grouping strategy
entirely) before being trustworthy — not a rejection, but a real,
separate design question that must not be silently defaulted.

## 4. Updated recommendation

The bridge described in the prior audit (§3 of
`PHASE_17GA2_MSI_STRUCTURE_ENGINES_AUDIT.md`) is now more precisely
scoped:

1. **5-minute intraday first** — `HistoricalObservationStore.range(instrument,
   RESOLUTION_FIVE_MINUTE, ...)` → `detect_price_change()` per
   consecutive pair → `process_event()`/`advance_time()` with
   **unmodified defaults** (they already fit) → `msi_price_structure`/
   `msi_market_structure`. One instrument stream at a time (INV-8).
   This is the lowest-risk, most directly reusable path.
2. **Daily deferred, or built with an explicit new window parameter** —
   not attempted with the 5-minute engine's defaults; needs its own
   deliberate decision (what window, or whether daily-resolution
   "episodes" are even the right grouping concept at all) before any
   code is written for it.

Nothing in this file's read changes the prior audit's core conclusion:
**perception logic and grouping logic both already exist, are
well-built, and are reusable as-is for 5-minute data.** The remaining
work is the bridge adapter (§3 of the prior doc), now known to be
straightforward for 5-min and a separate, real decision for daily.
