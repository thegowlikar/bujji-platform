"""Phase 20.27 -- Microstructure Intelligence Layer.

Interprets `bujji.market_microstructure.models.MinuteObservation`
(real OHLC + tick-density/silence/movement texture, Phase 19.20.2)
into a `MicrostructureReading` -- WITHOUT creating a new tick-capture
mechanism and WITHOUT modifying `bujji.market_microstructure` or
`bujji.mic_v0` (core).

STEP 1 AUDIT SUMMARY (full read-only audit already delivered earlier
this session -- summarized here for this package's own record):

- `bujji.market_microstructure` -- A) reusable directly. Real, tested
  (`tests/test_market_microstructure/`), produces real `MinuteObservation`
  records. Confirmed (2026-08-17 audit): built (Aug 16), but zero
  production callers anywhere in the codebase -- no live entrypoint
  feeds it real ticks, its SQLite store directory contains no database
  file. This phase does NOT change that; it only adds the missing
  interpretation step for whenever a live tick feed exists (Phase 20.28+).
- `bujji.intelligence.{regime_brain,structure_brain,volatility_brain}`
  -- D) missing capability for microstructure specifically (this
  phase's own gap), but A) reusable PATTERN: `Brain.analyze(...) ->
  Reading` shape, `DataQuality` enum (reused verbatim, not redefined),
  frozen dataclass Reading with `confidence: float`/`reason(s)`,
  priority-ordered deterministic classification with documented
  first-pass threshold constants. This phase follows that pattern; it
  does not duplicate any brain's own classification logic.
- `bujji.mic_context_bridge`'s own Phase 20.23 audit already confirmed,
  in writing, that no EXPANDING/CONTRACTING-style microstructure
  classification enum exists anywhere in the codebase -- this phase is
  exactly the missing piece that finding identified, built now with
  its own bounded `MicrostructureState` vocabulary.

This package NEVER opens a broker connection, NEVER reads a live tick
feed itself (fed already-produced `MinuteObservation`s by a caller,
same boundary `microstructure_aggregator.py` itself uses), NEVER
creates a trading signal or confidence value fed into any strategy
score, and is NOT wired into `bujji.mic_v0`, `live_shadow_runner`, or
any decision path by this phase -- Observation -> Intelligence only.
Phase 20.28 (tick feed) and Phase 20.29 (MIC consumption) are
deliberately separate, later phases.
"""
from .classifier import MicrostructureClassifier
from .explain import explain_microstructure_reading
from .models import DataQuality, MicrostructureReading, MicrostructureState

__all__ = [
    "MicrostructureClassifier", "explain_microstructure_reading",
    "DataQuality", "MicrostructureReading", "MicrostructureState",
]
