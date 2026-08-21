"""Formal Replay Engine -- Phase 15H. Pure models, no IO, no broker.

Forensic finding motivating this package: Phases 15B/15C/15D/15E/15F/15G
each independently built the SAME pattern -- read a real persisted
JSONL artifact, replay it through the exact production reducer, and
compare the result to what live code produced. This package
consolidates that pattern into one reusable contract, WITHOUT
reimplementing any of those reducers -- every domain function called
by `bujji.replay_engine.engine` is imported and called directly from
its own real module, never forked.

Naming adapted from this codebase's own conventions rather than the
generic textbook names: `RecoveryReport` (Phase 15B) is already this
project's "did hydration go cleanly" vocabulary, so `ReplaySession`
reuses that pattern rather than inventing a competing one.

CRITICAL, forensic-confirmed limitation (Step 4): `MarketSnapshot`
never persists the spot candles a cycle used -- they are fetched live
from the broker each cycle (`fetch_spot_candles`) and discarded.
Volatility Structure (VSB), and everything downstream of it (consensus,
opportunity, eligibility, selection, TradeIntent), therefore CANNOT be
independently reconstructed from `market_snapshots.jsonl` alone with
today's persisted artifacts. This is a REAL, disclosed architecture
gap, not silently worked around: those fields are treated as
REFERENCE data (read directly from the already-persisted
`intelligence_cycle.jsonl`, never recomputed), clearly distinguished
from RECONSTRUCTED fields (ObservationMemory/PSI/MSSI/MDI, Regime
Memory, Greeks, Premium Behaviour -- all independently replayable from
already-persisted artifacts through the same production functions).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

SCHEMA_VERSION = "1.0.0"

# --- Field reconstruction classification -------------------------------------
RECONSTRUCTED = "RECONSTRUCTED"  # independently rebuilt via the real production function, from persisted raw artifacts.
REFERENCED = "REFERENCED"        # read verbatim from the already-persisted intelligence_cycle.jsonl -- NOT recomputed (candle-coupling gap).

# --- Mismatch classification (Step 7) ----------------------------------------
CLASS_MATCH = "MATCH"
CLASS_MISMATCH = "MISMATCH"
CLASS_UNAVAILABLE = "UNAVAILABLE"
CLASS_NOT_APPLICABLE = "NOT_APPLICABLE"
ALL_MISMATCH_CLASSES = (CLASS_MATCH, CLASS_MISMATCH, CLASS_UNAVAILABLE, CLASS_NOT_APPLICABLE)

REASON_REPLAY_DEFECT = "REAL_REPLAY_DEFECT"
REASON_STALE_ARTIFACT = "STALE_ARTIFACT"
REASON_REPRESENTATION_ONLY = "REPRESENTATION_ONLY_DIFFERENCE"
REASON_EXPECTED_NONDETERMINISM = "EXPECTED_NONDETERMINISM"
REASON_MISSING_DEPENDENCY = "MISSING_REPLAY_DEPENDENCY"
REASON_SOURCE_DATA_DEFICIENCY = "SOURCE_DATA_DEFICIENCY"


@dataclass(frozen=True)
class MismatchRecord:
    field: str
    classification: str          # one of ALL_MISMATCH_CLASSES.
    reason_category: Optional[str]  # populated only for MISMATCH -- one of the REASON_* constants.
    detail: str

    def to_dict(self) -> dict:
        return {"field": self.field, "classification": self.classification,
                "reason_category": self.reason_category, "detail": self.detail}


@dataclass(frozen=True)
class ReplayCycleResult:
    """One cycle's replay output. `reconstructed` fields were computed
    by calling the REAL production function on the real persisted
    snapshot; `referenced` fields were read verbatim from the real
    persisted intelligence_cycle record -- both dicts are explicitly
    disjoint in key namespace so a caller never confuses the two."""

    cycle_index: int
    timestamp: str
    reconstructed: Dict[str, Any]
    referenced: Dict[str, Any]

    def to_dict(self) -> dict:
        return {"cycle_index": self.cycle_index, "timestamp": self.timestamp,
                "reconstructed": self.reconstructed, "referenced": self.referenced}


@dataclass(frozen=True)
class ReplayCheckpoint:
    """The ENTIRE checkpoint is just how many real cycles have been
    consumed so far -- resuming means re-hydrating from the SAME real
    persisted files up to that count (the exact mechanism Phase
    15D/15E/15G's own hydration functions already are), never a
    separately-serialized blob of mutable state."""

    cycle_index: int
    session_id: str

    def to_dict(self) -> dict:
        return {"cycle_index": self.cycle_index, "session_id": self.session_id}


@dataclass(frozen=True)
class ReplaySession:
    session_id: str
    market_snapshot_path: str
    intelligence_cycle_path: Optional[str]
    cycles: Tuple[ReplayCycleResult, ...]
    final_fingerprint: str
    recovery_reports: Dict[str, Any]  # domain name -> RecoveryReport.to_dict(), e.g. {"observation_memory": {...}, "premium_behaviour": {...}}.
    schema_version: str = SCHEMA_VERSION

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id, "market_snapshot_path": self.market_snapshot_path,
            "intelligence_cycle_path": self.intelligence_cycle_path,
            "cycles": [c.to_dict() for c in self.cycles], "final_fingerprint": self.final_fingerprint,
            "recovery_reports": self.recovery_reports, "schema_version": self.schema_version,
        }
