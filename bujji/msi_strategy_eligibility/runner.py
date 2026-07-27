"""Strategy Eligibility runner — public composition entrypoints.

---------------------------------------------------------------------
Batch-vs-incremental framing (Step 1's explicit design question,
resolved here).
---------------------------------------------------------------------
Series 78/79/81's runners thread a `previous_assessment` through a
SEQUENCE of cycles, so "batch vs incremental parity" there means
"replaying a sequence all at once produces the same per-cycle results,
byte-for-byte, as feeding the same cycles one at a time live" --
meaningful because each cycle's output depends on the PRIOR cycle's
output (`what_changed` threading).

`engine.determine_eligibility()` is different in kind: it is a PURE,
STATELESS function of exactly one `(opportunity, consensus)` pair --
it has no `previous_assessment` parameter at all, because Deliverable
2's `StrategyEligibilityAssessment` carries no `what_changed`-style
field and there is no principled definition of "the previous
eligibility assessment" independent of "the previous opportunity/
consensus pair" (unlike 77/81, whose own internal state genuinely
carries forward). Forcing 78/79's sequence-threading batch/incremental
shape onto a single-assessment-in/single-assessment-out function would
manufacture state that does not exist.

The parity property that IS meaningful and IS tested here is
therefore: "the same `(opportunity, consensus)` pair, fed through
`determine_eligibility()` via the batch entrypoint and via the
incremental/streaming entrypoint, at two different wall-clock
`timestamp`s, produces byte-identical results for every field except
`timestamp`" -- i.e. determinism-under-repetition, not determinism-
under-sequence-threading. Both entrypoints below still exist (batch
processes a `Sequence` of independent pairs; the streaming class
processes them one at a time) because a real caller does need both
shapes operationally (an offline replay over many cycles vs. a live
per-cycle call) -- but both delegate to the exact same pure
`engine.determine_eligibility` function per pair, so parity holds by
construction, exactly as 78/79/81 established for their own shape.
"""
from __future__ import annotations

from typing import List, Optional, Protocol, Sequence, Tuple, runtime_checkable

from bujji.msi_consensus.models import ConsensusAssessment
from bujji.msi_decision_synthesis.models import MarketOpportunityAssessment

from . import config as _config
from . import engine
from .models import StrategyEligibilityAssessment


@runtime_checkable
class StrategyEligibilityPublisher(Protocol):
    def publish(self, assessment: StrategyEligibilityAssessment) -> None: ...


class InMemoryStrategyEligibilityPublisher:
    """No-op-safe, in-memory-collecting default publisher -- for
    tests/demonstration only, never a stand-in for a real downstream
    consumer."""

    def __init__(self) -> None:
        self.published: List[StrategyEligibilityAssessment] = []

    def publish(self, assessment: StrategyEligibilityAssessment) -> None:
        self.published.append(assessment)


def _record_and_publish(assessment: StrategyEligibilityAssessment, *, journal=None, publisher: Optional[StrategyEligibilityPublisher] = None) -> None:
    if journal is not None:
        journal.record_assessment(assessment)
    if publisher is not None:
        publisher.publish(assessment)


def determine_eligibility_for_cycles(
    pairs: Sequence[Tuple[MarketOpportunityAssessment, ConsensusAssessment]],
    *,
    timestamps: Sequence[str],
    schema_version: str = _config.SCHEMA_VERSION,
    journal=None,
    publisher: Optional[StrategyEligibilityPublisher] = None,
) -> Tuple[StrategyEligibilityAssessment, ...]:
    """`pairs` is an ordered sequence of independent (opportunity,
    consensus) inputs, one per cycle. Produces one
    StrategyEligibilityAssessment per cycle. No state threads between
    cycles (see module docstring)."""
    assessments: List[StrategyEligibilityAssessment] = []
    provenance = "msi_strategy_eligibility.runner.determine_eligibility_for_cycles[REPLAY]"
    for (opportunity, consensus), timestamp in zip(pairs, timestamps):
        assessment = engine.determine_eligibility(
            opportunity, consensus, timestamp=timestamp, schema_version=schema_version, provenance=provenance,
        )
        assessments.append(assessment)
        _record_and_publish(assessment, journal=journal, publisher=publisher)
    return tuple(assessments)


class StrategyEligibilityStream:
    """Live, per-cycle counterpart to `determine_eligibility_for_cycles`.
    Delegates to the exact same pure `engine.determine_eligibility`
    function per call -- parity with the batch entrypoint holds by
    construction."""

    def __init__(
        self,
        *,
        schema_version: str = _config.SCHEMA_VERSION,
        journal=None,
        publisher: Optional[StrategyEligibilityPublisher] = None,
    ) -> None:
        self._schema_version = schema_version
        self.journal = journal
        self.publisher = publisher
        self._latest: Optional[StrategyEligibilityAssessment] = None

    @property
    def latest(self) -> Optional[StrategyEligibilityAssessment]:
        return self._latest

    def handle_pair(
        self,
        opportunity: MarketOpportunityAssessment,
        consensus: ConsensusAssessment,
        *,
        timestamp: str,
    ) -> StrategyEligibilityAssessment:
        assessment = engine.determine_eligibility(
            opportunity, consensus, timestamp=timestamp, schema_version=self._schema_version,
            provenance="msi_strategy_eligibility.runner.StrategyEligibilityStream[LIVE]",
        )
        _record_and_publish(assessment, journal=self.journal, publisher=self.publisher)
        self._latest = assessment
        return assessment
