"""Trade Intent runner — public composition entrypoints.

---------------------------------------------------------------------
Batch-vs-incremental framing (same design question Series 82 resolved,
resolved here identically).
---------------------------------------------------------------------
`engine.determine_trade_intent()` is a PURE, STATELESS function of
exactly one `(eligibility, opportunity)` pair -- it has no
`previous_assessment` parameter, because `TradeIntentAssessment`
carries no `what_changed`-style field and there is no principled
definition of "the previous trade intent" independent of "the previous
eligibility/opportunity pair" (mirrors 82's own runner.py precedent
exactly, itself mirroring 78/79/81's original resolution of the same
question for their own shape).

The parity property that IS meaningful and IS tested here is
therefore: "the same `(eligibility, opportunity)` pair, fed through
`determine_trade_intent()` via the batch entrypoint and via the
incremental/streaming entrypoint, at two different wall-clock
`timestamp`s, produces byte-identical results for every field except
`timestamp`" -- i.e. determinism-under-repetition, not determinism-
under-sequence-threading. Both entrypoints below still exist (batch
processes a `Sequence` of independent pairs; the streaming class
processes them one at a time) because a real caller does need both
shapes operationally -- but both delegate to the exact same pure
`engine.determine_trade_intent` function per pair, so parity holds by
construction.
"""
from __future__ import annotations

from typing import List, Optional, Protocol, Sequence, Tuple, runtime_checkable

from bujji.msi_decision_synthesis.models import MarketOpportunityAssessment
from bujji.msi_strategy_eligibility.models import StrategyEligibilityAssessment

from . import config as _config
from . import engine
from .models import TradeIntentAssessment


@runtime_checkable
class TradeIntentPublisher(Protocol):
    def publish(self, assessment: TradeIntentAssessment) -> None: ...


class InMemoryTradeIntentPublisher:
    """No-op-safe, in-memory-collecting default publisher -- for
    tests/demonstration only, never a stand-in for a real downstream
    consumer."""

    def __init__(self) -> None:
        self.published: List[TradeIntentAssessment] = []

    def publish(self, assessment: TradeIntentAssessment) -> None:
        self.published.append(assessment)


def _record_and_publish(assessment: Optional[TradeIntentAssessment], *, journal=None, publisher: Optional[TradeIntentPublisher] = None) -> None:
    if assessment is None:
        return
    if journal is not None:
        journal.record_assessment(assessment)
    if publisher is not None:
        publisher.publish(assessment)


def determine_trade_intent_for_cycles(
    pairs: Sequence[Tuple[StrategyEligibilityAssessment, MarketOpportunityAssessment]],
    *,
    timestamps: Sequence[str],
    schema_version: str = _config.SCHEMA_VERSION,
    journal=None,
    publisher: Optional[TradeIntentPublisher] = None,
) -> Tuple[Optional[TradeIntentAssessment], ...]:
    """`pairs` is an ordered sequence of independent (eligibility,
    opportunity) inputs, one per cycle. Produces one
    TradeIntentAssessment (or None, if no family could be selected)
    per cycle. No state threads between cycles (see module
    docstring)."""
    assessments: List[Optional[TradeIntentAssessment]] = []
    provenance = "msi_trade_intent.runner.determine_trade_intent_for_cycles[REPLAY]"
    for (eligibility, opportunity), timestamp in zip(pairs, timestamps):
        assessment = engine.determine_trade_intent(
            eligibility, opportunity, timestamp=timestamp, schema_version=schema_version, provenance=provenance,
        )
        assessments.append(assessment)
        _record_and_publish(assessment, journal=journal, publisher=publisher)
    return tuple(assessments)


class TradeIntentStream:
    """Live, per-cycle counterpart to
    `determine_trade_intent_for_cycles`. Delegates to the exact same
    pure `engine.determine_trade_intent` function per call -- parity
    with the batch entrypoint holds by construction."""

    def __init__(
        self,
        *,
        schema_version: str = _config.SCHEMA_VERSION,
        journal=None,
        publisher: Optional[TradeIntentPublisher] = None,
    ) -> None:
        self._schema_version = schema_version
        self.journal = journal
        self.publisher = publisher
        self._latest: Optional[TradeIntentAssessment] = None

    @property
    def latest(self) -> Optional[TradeIntentAssessment]:
        return self._latest

    def handle_pair(
        self,
        eligibility: StrategyEligibilityAssessment,
        opportunity: MarketOpportunityAssessment,
        *,
        timestamp: str,
    ) -> Optional[TradeIntentAssessment]:
        assessment = engine.determine_trade_intent(
            eligibility, opportunity, timestamp=timestamp, schema_version=self._schema_version,
            provenance="msi_trade_intent.runner.TradeIntentStream[LIVE]",
        )
        _record_and_publish(assessment, journal=self.journal, publisher=self.publisher)
        self._latest = assessment
        return assessment
