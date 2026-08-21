"""Live Cycle Completeness Gate -- Shadow Runtime, Phase 19.13.

Task 5, verbatim: "Incomplete reality must never produce falsely
confident intelligence." The six Phase 19.0-19.2 brains already gate
their OWN internal confidence honestly (UNKNOWN/INSUFFICIENT states,
unmodified here) -- this module is the layer ABOVE them: it decides
whether a `MarketRealitySnapshot` has enough real evidence to be worth
composing intelligence from AT ALL, before `build_intelligence_heartbeat_cycle`
(Phase 19.10.1, unmodified) is ever called.

Deliberately NOT gating on `MarketRealitySnapshot.completeness ==
COMPLETENESS_PARTIAL` -- `reality_translator.py` (Phase 19.10.2)
ALWAYS marks a live single-tick snapshot PARTIAL by honest design (a
point observation is never a full day's OHLC), so that would gate every
live cycle unconditionally, which is not what "incomplete" means here.
The gate instead checks for the genuinely disqualifying case: no real
spot price and/or no real options chain to reason from -- exactly the
two preconditions `IntelligencePipelineAdapterError`
(`intelligence_pipeline_adapter.py`) already treats as "nothing to
compose from" when raised deep inside composition. Checking BEFORE
composition, rather than only catching that exception after, lets a
caller record an honest, structured gate-failure reason instead of a
generic exception message.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

from bujji.market_reality_snapshot.models import COMPLETENESS_EMPTY, MarketRealitySnapshot


@dataclass(frozen=True)
class CompletenessGateResult:
    passed: bool
    reasons: Tuple[str, ...]

    def to_dict(self) -> dict:
        return {"passed": self.passed, "reasons": list(self.reasons)}


def evaluate_completeness_gate(reality_snapshot: MarketRealitySnapshot) -> CompletenessGateResult:
    reasons = []
    if reality_snapshot.completeness == COMPLETENESS_EMPTY:
        reasons.append("reality_snapshot.completeness is EMPTY -- no instrument observed at all")
    if reality_snapshot.spot is None:
        reasons.append("reality_snapshot has no spot -- nothing to build a regime/structure view from")
    if reality_snapshot.options is None or len(reality_snapshot.options.contracts) == 0:
        reasons.append("reality_snapshot has no options chain -- nothing to build a volatility/greeks/liquidity view from")
    return CompletenessGateResult(passed=len(reasons) == 0, reasons=tuple(reasons))
