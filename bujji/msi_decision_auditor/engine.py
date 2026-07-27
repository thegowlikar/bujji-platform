"""Decision Auditor & Learning Observatory engine — Series 99.

Two pure builder functions, `build_decision_record` and
`build_outcome_record`, plus `link` to pair them deterministically.
Consumes ONLY real, already-computed assessment objects from every
upstream MSI package (Series 78/79/85/86/88/89/92/95/91/96/97/98) --
never re-derives any of their reasoning, never scores, never ranks,
never adds any learning of any kind.
"""
from __future__ import annotations

import hashlib
from typing import Optional, Sequence, Tuple

from bujji.intelligence.regime_brain import RegimeBrain
from bujji.msi_execution_planning.models import ExecutionPlanAssessment
from bujji.msi_margin_bridge.models import MarginEstimate
from bujji.msi_portfolio_construction.models import PortfolioConstructionAssessment
from bujji.msi_position_construction.models import PositionConstructionAssessment
from bujji.msi_position_lifecycle.taxonomy import COMPATIBLE_THESIS_TRANSITIONS
from bujji.msi_trade_thesis.models import TradeThesisAssessment

from . import taxonomy
from .models import (
    DecisionExplanation, DecisionOutcomePair, DecisionRecord, OutcomeExplanation, OutcomeRecord,
)


def _decision_id(date: str, thesis_id: str, family: Optional[str], schema_version: str) -> str:
    content = "|".join([date, thesis_id, family or "", schema_version])
    return hashlib.md5(content.encode("utf-8")).hexdigest()


def build_decision_record(
    date: str, observation_ids: Tuple[str, ...], episode_ids: Tuple[str, ...],
    market_direction: str, consensus: str, volatility_state: str,
    trade_thesis: TradeThesisAssessment, strategy_family: Optional[str],
    position_construction: Optional[PositionConstructionAssessment],
    portfolio_decision: Optional[PortfolioConstructionAssessment],
    lifecycle_state: Optional[str], margin_assessment: Optional[MarginEstimate],
    execution_plan: Optional[ExecutionPlanAssessment], *, timestamp: str,
) -> DecisionRecord:
    """Produces exactly one DecisionRecord for one real day. Never
    changes any input; only records references to real, already-
    computed assessments."""
    schema_version = taxonomy.MSI_DECISION_AUDITOR_VERSION

    decision_outcome = (
        taxonomy.DECISION_TRADE_APPROVED
        if portfolio_decision is not None and portfolio_decision.approval_state == "APPROVED"
        else taxonomy.DECISION_NO_TRADE
    )
    confidence = trade_thesis.conviction  # Real pass-through -- never re-derived, never a new score.

    why = [f"thesis={trade_thesis.thesis_type} (conviction={confidence})"]
    if strategy_family is not None:
        why.append(f"strategy_family={strategy_family}")
    else:
        why.append("no strategy family was selected -- recorded as NO_TRADE")
    if portfolio_decision is not None:
        why.append(f"portfolio_decision={portfolio_decision.approval_state} "
                   f"({', '.join(portfolio_decision.rejection_reasons) or 'no rejection reasons'})")
    if execution_plan is not None:
        why.append(f"execution_plan produced: {len(execution_plan.execution_steps)} stage(s)")
    else:
        why.append("no execution plan was produced")

    aid = _decision_id(date, trade_thesis.assessment_id, strategy_family, schema_version)
    explanation = DecisionExplanation(assessment_id=aid, why=tuple(why), schema_version=schema_version)

    return DecisionRecord(
        decision_id=aid, timestamp=timestamp, date=date, observation_ids=observation_ids,
        episode_ids=episode_ids, market_direction=market_direction, consensus=consensus,
        volatility_state=volatility_state, trade_thesis=trade_thesis, strategy_family=strategy_family,
        position_construction=position_construction, portfolio_decision=portfolio_decision,
        lifecycle_state=lifecycle_state, margin_assessment=margin_assessment, execution_plan=execution_plan,
        confidence=confidence, decision_outcome=decision_outcome, explanation=explanation,
        provenance="bujji.msi_decision_auditor.engine.build_decision_record", schema_version=schema_version,
    )


def _outcome_id(date: str, close: Optional[float], schema_version: str) -> str:
    content = "|".join([date, str(close), schema_version])
    return hashlib.md5(content.encode("utf-8")).hexdigest()


def build_outcome_record(
    date: str, closes: Sequence[float], entry_thesis_type: str,
    next_day_thesis_type: Optional[str], execution_plan_present: bool, *, timestamp: str,
) -> OutcomeRecord:
    """Records what actually happened on `date` -- real intraday closes
    only (this real corpus's intraday reconstruction has no separate
    high/low field; session_high/low are therefore the max/min of the
    real CLOSE series, a disclosed proxy, not true intrabar extremes).
    Never evaluates whether the original decision was RIGHT -- only
    what happened."""
    schema_version = taxonomy.MSI_DECISION_AUDITOR_VERSION

    if not closes:
        aid = _outcome_id(date, None, schema_version)
        explanation = OutcomeExplanation(
            assessment_id=aid, what_actually_happened=("no real intraday closes were available for this day",),
            schema_version=schema_version,
        )
        return OutcomeRecord(
            outcome_id=aid, timestamp=timestamp, date=date, close_price=None, session_high=None,
            session_low=None, realised_movement_pct=None, realised_volatility=None,
            realised_direction=taxonomy.REALISED_FLAT, thesis_survival=taxonomy.SURVIVAL_UNKNOWN,
            execution_feasibility=taxonomy.FEASIBILITY_PLAN_PRODUCED if execution_plan_present else taxonomy.FEASIBILITY_NO_PLAN,
            explanation=explanation, provenance="bujji.msi_decision_auditor.engine.build_outcome_record",
            schema_version=schema_version,
        )

    open_proxy = closes[0]
    close_price = closes[-1]
    session_high = max(closes)
    session_low = min(closes)
    movement_pct = round((close_price - open_proxy) / open_proxy * 100.0, 4) if open_proxy else None

    returns = RegimeBrain._log_returns(list(closes))
    realised_volatility = round(RegimeBrain._stdev(returns), 6) if len(returns) >= 2 else None

    if movement_pct is None or abs(movement_pct) < 0.01:
        realised_direction = taxonomy.REALISED_FLAT
    elif movement_pct > 0:
        realised_direction = taxonomy.REALISED_UP
    else:
        realised_direction = taxonomy.REALISED_DOWN

    if next_day_thesis_type is None:
        thesis_survival = taxonomy.SURVIVAL_UNKNOWN
    else:
        compatible_set = COMPATIBLE_THESIS_TRANSITIONS.get(entry_thesis_type, (entry_thesis_type,))
        survived = (
            next_day_thesis_type in compatible_set
            or (entry_thesis_type == "EVENT_RISK" and next_day_thesis_type != "NO_TRADE")
        )
        thesis_survival = "SURVIVED" if survived else "INVALIDATED"

    execution_feasibility = taxonomy.FEASIBILITY_PLAN_PRODUCED if execution_plan_present else taxonomy.FEASIBILITY_NO_PLAN

    what_happened = [
        f"session (real, close-price-only proxy): open~{open_proxy}, close={close_price}, "
        f"high(of closes)={session_high}, low(of closes)={session_low}",
        f"realised movement: {movement_pct}% ({realised_direction})",
    ]
    if realised_volatility is not None:
        what_happened.append(f"realised volatility (stdev of log returns): {realised_volatility}")
    if thesis_survival != taxonomy.SURVIVAL_UNKNOWN:
        what_happened.append(f"next-day thesis was {next_day_thesis_type} -- entry thesis "
                             f"{'SURVIVED' if thesis_survival == 'SURVIVED' else 'was INVALIDATED'}")

    aid = _outcome_id(date, close_price, schema_version)
    explanation = OutcomeExplanation(assessment_id=aid, what_actually_happened=tuple(what_happened), schema_version=schema_version)

    return OutcomeRecord(
        outcome_id=aid, timestamp=timestamp, date=date, close_price=close_price, session_high=session_high,
        session_low=session_low, realised_movement_pct=movement_pct, realised_volatility=realised_volatility,
        realised_direction=realised_direction, thesis_survival=thesis_survival,
        execution_feasibility=execution_feasibility, explanation=explanation,
        provenance="bujji.msi_decision_auditor.engine.build_outcome_record", schema_version=schema_version,
    )


def link(decision: DecisionRecord, outcome: OutcomeRecord) -> DecisionOutcomePair:
    """Deterministic, unambiguous 1:1 pairing -- never fuzzy-matched.
    Fails loudly (AssertionError) if the caller ever tries to pair
    records from different dates, rather than silently pairing the
    wrong day."""
    if decision.date != outcome.date:
        raise ValueError(f"cannot link DecisionRecord(date={decision.date}) to "
                         f"OutcomeRecord(date={outcome.date}) -- dates must match exactly")
    schema_version = taxonomy.MSI_DECISION_AUDITOR_VERSION
    pair_id = hashlib.md5(f"{decision.decision_id}|{outcome.outcome_id}|{schema_version}".encode("utf-8")).hexdigest()
    return DecisionOutcomePair(
        pair_id=pair_id, decision=decision, outcome=outcome,
        provenance="bujji.msi_decision_auditor.engine.link", schema_version=schema_version,
    )
