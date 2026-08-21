"""IntelligenceContext — Phase 19.2.2.

The ONE shared clock/reference concept every brain now accepts,
replacing six independent `now_ist()` calls with one injected value.
Modeled directly on `mil_next.snapshot_builder`'s own already-proven
`Clock = Callable[[], datetime]` pattern (Phase 19.1.2's own confirmed
finding) -- reused as a design, not as code (mil_next's own package
stays untouched, per Phase 19.1.1's governance rule).

WHY A SHARED OBJECT AND NOT SIX SEPARATE `as_of_time` PARAMETERS: this
phase's own explicit instruction ("do not introduce six different
clock systems") -- one object threads the same `as_of_time` plus the
Reality/Dataset references every brain's evidence should point back to
(Phase 19.2.1's own evidence-lineage finding) through every brain
uniformly, so a future `MarketIntelligenceSnapshot` builder never has
to reconcile six independently-shaped clock arguments.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

EXECUTION_MODE_LIVE = "LIVE"
EXECUTION_MODE_PAPER = "PAPER"
EXECUTION_MODE_HISTORICAL_REPLAY = "HISTORICAL_REPLAY"
ALL_EXECUTION_MODES = (EXECUTION_MODE_LIVE, EXECUTION_MODE_PAPER, EXECUTION_MODE_HISTORICAL_REPLAY)


@dataclass(frozen=True)
class IntelligenceContext:
    """Passed into every brain's `analyze()` call, required, no
    default -- a default of "now" would silently reintroduce exactly
    the bug this phase exists to close (Phase 19.2.1's own confirmed
    six-for-six finding)."""

    as_of_time: datetime
    # The real MarketRealitySnapshot.fingerprint() this reading's inputs
    # were sourced from, when known -- Optional because a brain can be
    # called from data that never went through snapshot reconstruction
    # (e.g. a raw unit-test fixture); an honest absence, never fabricated.
    reality_snapshot_reference: Optional[str] = None
    # The real DatasetArtifact.artifact_id, when the call is made
    # against a published, eligible artifact (Phase 18.12/18.14) --
    # Optional for the same reason as above.
    dataset_artifact_reference: Optional[str] = None
    execution_mode: str = EXECUTION_MODE_LIVE

    def __post_init__(self) -> None:
        if self.execution_mode not in ALL_EXECUTION_MODES:
            raise ValueError(
                f"execution_mode={self.execution_mode!r} not in {ALL_EXECUTION_MODES} -- "
                "fails closed rather than silently accepting an unknown mode."
            )

    def to_dict(self) -> dict:
        return {
            "as_of_time": self.as_of_time.isoformat(),
            "reality_snapshot_reference": self.reality_snapshot_reference,
            "dataset_artifact_reference": self.dataset_artifact_reference,
            "execution_mode": self.execution_mode,
        }
