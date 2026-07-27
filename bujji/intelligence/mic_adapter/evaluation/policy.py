"""Intelligence Policy — BUJJI Options OS, Integration Series 3, Sprint 1.

A finite agreement taxonomy for comparing BUJJI's actual production
decision against MIC v2's published intelligence, plus the deliberately
conservative reference policy that classifies each comparison. The
policy never generates a trade, never loads market data or broker
state, never reads positions or PnL, never calls a MIC v2 reasoning
engine, and never recomputes intelligence -- it only classifies what the
ALREADY-PUBLISHED reference data (Sprint 1's `IntelligenceSnapshot`) is
sufficient to say.

Honesty note (see docs/INTELLIGENCE_EVALUATION_ARCHITECTURE.md): as of
Integration Series 3, Sprint 1, MIC v2's Consumer API published only
reference ids and an availability status -- no directional opinion --
so `default_opinion_source` always returned None. As of Integration
Series 4, Sprint 1, MIC v2 (Engineering Series 19, Sprint 1 / Addendum
8) now publishes a real `MarketOpinion` reference, and
`bujji.intelligence.mic_adapter.opinion_reader.read_opinion_classification()`
resolves it. `default_opinion_source` remains unchanged (still always
returns None) -- it is the policy's own safe fallback, never
constructed with a real reader by default; the real reader is wired in
explicitly at `Orchestrator.__init__` (see
`docs/OPINION_SOURCE_WIRING_ARCHITECTURE.md`).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Optional

from .metrics import EvaluationProvenance

EVALUATION_TAXONOMY_VERSION = "1.0.0"

AGREED = "AGREED"
DISAGREED = "DISAGREED"
ABSTAINED = "ABSTAINED"
INSUFFICIENT_INTELLIGENCE = "INSUFFICIENT_INTELLIGENCE"
UNKNOWN = "UNKNOWN"

EVALUATION_OUTCOMES = (AGREED, DISAGREED, ABSTAINED, INSUFFICIENT_INTELLIGENCE, UNKNOWN)


@dataclass(frozen=True)
class IntelligenceEvaluation:
    """One production decision's comparison against published MIC v2
    intelligence -- immutable, never mutated after construction. Not a
    trading record: carries no price, quantity, order, or PnL field."""

    evaluation_id: str
    decision_id: str
    timestamp: datetime
    outcome: str                          # One of the finite taxonomy above.
    reason: str
    production_direction: Optional[str]   # "BULLISH" | "BEARISH" | "NEUTRAL" | None, copied verbatim.
    snapshot_available: bool
    snapshot_id: Optional[str]
    provenance: EvaluationProvenance


def default_opinion_source(
    consumer_status: Optional[str], snapshot_id: Optional[str], market_opinion_id: Optional[str] = None,
) -> Optional[str]:
    """The policy's safe fallback -- always returns None regardless of
    input. A caller wanting real opinions must construct
    `IntelligencePolicy` with a different `opinion_source` (see
    `bujji.intelligence.mic_adapter.opinion_reader.read_opinion_classification`,
    Integration Series 4, Sprint 1)."""
    return None


def classify_evaluation(
    feature_flag_enabled: bool,
    snapshot_available: bool,
    consumer_status: Optional[str],
    production_direction: Optional[str],
    opinion: Optional[str],
) -> tuple[str, str]:
    """Pure classification -- no I/O, no side effects. Returns
    (outcome, reason). Priority order: flag -> availability -> status ->
    production direction -> opinion -> agreement."""
    if not feature_flag_enabled:
        return UNKNOWN, "evaluation_disabled"

    if not snapshot_available:
        return INSUFFICIENT_INTELLIGENCE, "no_snapshot_available"

    if consumer_status != "AVAILABLE":
        return INSUFFICIENT_INTELLIGENCE, f"consumer_status={consumer_status}"

    if production_direction is None:
        return ABSTAINED, "no_production_direction"

    if opinion is None:
        return ABSTAINED, "no_directional_opinion_published"

    if opinion == production_direction:
        return AGREED, "opinion_matches_production_direction"

    return DISAGREED, "opinion_differs_from_production_direction"


class IntelligencePolicy:
    """The reference policy. Deliberately conservative: never generates
    a trade, only classifies sufficiency to agree/disagree/abstain using
    already-published reference data."""

    def __init__(
        self,
        opinion_source: Callable[[Optional[str], Optional[str], Optional[str]], Optional[str]] = default_opinion_source,
    ) -> None:
        self._opinion_source = opinion_source

    def classify(
        self,
        feature_flag_enabled: bool,
        snapshot_available: bool,
        consumer_status: Optional[str],
        production_direction: Optional[str],
        snapshot_id: Optional[str],
        market_opinion_id: Optional[str] = None,
    ) -> tuple[str, str]:
        opinion = self._opinion_source(consumer_status, snapshot_id, market_opinion_id) if snapshot_available else None
        return classify_evaluation(
            feature_flag_enabled, snapshot_available, consumer_status, production_direction, opinion,
        )
