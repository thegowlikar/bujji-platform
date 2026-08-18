"""Phase 20.6 -- declarative qualification rules. Pure functions,
fixed disclosed thresholds, no ML, no optimization, no parameter
search. Every threshold is a round, pre-registered reference point,
not fit to any specific strategy's Phase 20.4/20.5 numbers.
"""
from __future__ import annotations

from typing import Optional, Tuple

from bujji.epistemics import uncertainty as epi
from bujji.mic_v0 import models as mic_models
from bujji.strategy_intelligence import StrategyScore

from .models import MarketEnvironment, QualificationReason

# Below this effective_score (out of 100) OR at NONE confidence, a
# strategy is INSUFFICIENT_EVIDENCE regardless of how good the market
# looks -- checked FIRST, before any environment rule, so MIC can
# never elevate an unconfirmed strategy into consideration.
MIN_SUFFICIENT_EFFECTIVE_SCORE = 30.0

# Minimum effective_score for ELIGIBLE (not just WATCH) even in an
# ideal environment -- matches this phase's own worked example
# ("Score > 70 ... Result: ELIGIBLE").
MIN_ELIGIBLE_EFFECTIVE_SCORE = 70.0

# Execution drag fraction, under an EXTREME execution-profile
# assumption, above which a strategy is BLOCKED as execution-
# impossible rather than merely WATCH-worthy.
EXTREME_EXECUTION_DRAG_BLOCK_THRESHOLD = 0.5


def has_sufficient_evidence(score: StrategyScore) -> bool:
    return score.confidence != epi.NONE and score.effective_score >= MIN_SUFFICIENT_EFFECTIVE_SCORE


def insufficient_evidence_reason(score: StrategyScore) -> QualificationReason:
    return QualificationReason(
        code="INSUFFICIENT_EVIDENCE",
        detail=f"confidence={score.confidence}, effective_score={score.effective_score} "
               f"(n={score.evidence.sample_size}) -- below the minimum bar for consideration",
    )


def _strategy_execution_drag_pct(score: StrategyScore) -> Optional[float]:
    e = score.evidence
    if not e.gross_expectancy or e.gross_expectancy <= 0 or e.net_expectancy is None:
        return None
    return max(0.0, (e.gross_expectancy - e.net_expectancy) / e.gross_expectancy)


def hard_block_reason(
    score: StrategyScore, environment: MarketEnvironment,
    unfavorable_regimes: Tuple[str, ...] = (),
) -> Optional[QualificationReason]:
    """Any ONE of these overrides everything else -- "one hard blocker
    overrides everything," the same gate discipline `decision_context.
    compatibility_engine` (Phase 19.4) already established for a
    different domain. Checked only AFTER evidence sufficiency, so a
    strategy that never even clears the evidence bar reports
    INSUFFICIENT_EVIDENCE, not a confusing BLOCKED."""
    if environment.risk_state == mic_models.RISK_EXTREME:
        return QualificationReason(code="EXTREME_RISK", detail=f"risk_state={environment.risk_state}")
    if not environment.data_quality_ok:
        return QualificationReason(code="DATA_QUALITY", detail="environment data quality flagged not OK")
    if environment.mic_regime in unfavorable_regimes:
        return QualificationReason(
            code="REGIME_INCOMPATIBLE",
            detail=f"mic_regime={environment.mic_regime!r} explicitly disclosed as incompatible with this strategy",
        )
    if environment.execution_profile_name == "EXTREME":
        drag = _strategy_execution_drag_pct(score)
        if drag is not None and drag >= EXTREME_EXECUTION_DRAG_BLOCK_THRESHOLD:
            return QualificationReason(
                code="EXECUTION_IMPOSSIBLE",
                detail=f"historical execution drag {drag:.0%} under an EXTREME execution-profile assumption "
                       f"-- realistically untradeable",
            )
    return None


def is_ideal_environment(environment: MarketEnvironment, favorable_regimes: Tuple[str, ...]) -> bool:
    return (
        environment.mic_regime in favorable_regimes
        and environment.risk_state == mic_models.RISK_NORMAL
        and environment.volatility_state in (mic_models.VOLATILITY_LOW, mic_models.VOLATILITY_NORMAL)
        and environment.execution_profile_name == "NORMAL"
    )


def suboptimal_environment_reason(environment: MarketEnvironment, favorable_regimes: Tuple[str, ...]) -> QualificationReason:
    if environment.mic_regime not in favorable_regimes:
        detail = f"mic_regime={environment.mic_regime!r} not in this strategy's validated favorable set {favorable_regimes!r}"
    elif environment.risk_state != mic_models.RISK_NORMAL:
        detail = f"risk_state={environment.risk_state} (not NORMAL)"
    elif environment.volatility_state == mic_models.VOLATILITY_HIGH:
        detail = f"volatility_state={environment.volatility_state}"
    else:
        detail = f"execution_profile={environment.execution_profile_name} (not NORMAL)"
    return QualificationReason(code="SUBOPTIMAL_ENVIRONMENT", detail=detail)


def qualification_rules() -> Tuple[str, ...]:
    """The fixed rule ORDER this module applies, disclosed as data so
    a caller (or the report) can print it without re-deriving it from
    source: evidence sufficiency first, then hard blocks, then ideal-
    match ELIGIBLE, else WATCH."""
    return (
        "1. INSUFFICIENT_EVIDENCE if confidence == NONE or effective_score < "
        f"{MIN_SUFFICIENT_EFFECTIVE_SCORE} -- checked before any environment rule.",
        "2. BLOCKED if risk_state == EXTREME, or data quality not OK, or the current regime is explicitly "
        "disclosed as unfavorable for this strategy, or execution drag under an EXTREME profile is >= "
        f"{EXTREME_EXECUTION_DRAG_BLOCK_THRESHOLD:.0%}.",
        f"3. ELIGIBLE if the regime is in this strategy's favorable set AND risk/volatility/execution are all "
        f"normal AND effective_score >= {MIN_ELIGIBLE_EFFECTIVE_SCORE}.",
        "4. WATCH otherwise -- evidence is sufficient and nothing is blocked, but the environment is not ideal.",
    )
