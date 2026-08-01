"""snapshot_builder -- BUJJI MIL Next.

`build_snapshot(...)` is the SOLE public entry point for producing a
MarketIntelligenceSnapshot. It owns: data-quality assembly, the
minimal NO_TRADE path, timeframe fold orchestration, composed-regime
assembly, competing-thesis derivation, contradiction scoring, OI
downgrade, event-calendar resolution, and posture-table evaluation.

Tests must call this function -- never hand-construct a
MarketIntelligenceSnapshot with pre-decided field values.
"""
from __future__ import annotations

import hashlib
from datetime import date, datetime, timezone
from typing import Callable, Dict, Optional

from . import taxonomy as tx
from .canonical_hash import compute_content_hash
from .data_quality import build_data_quality_context, is_mandatory_failure
from .event_calendar import EventCalendarSource, load_calendar_view, resolve_event_risk_state
from .models import (
    ComposedRegimeView,
    ContradictionScore,
    DataQualityContext,
    EventCalendarView,
    MarketDataInputs,
    MarketIntelligenceSnapshot,
    OIContext,
    TimeframeConfig,
    TradeThesis,
)
from .timeframe_fold import compute_timeframe_agreement, fold_timeframe

Clock = Callable[[], datetime]


def _real_clock() -> datetime:
    return datetime.now(timezone.utc)


def _snapshot_id(session_id: str, cadence_id: str, decision_cutoff_event_time: datetime) -> str:
    seed = f"{session_id}|{cadence_id}|{decision_cutoff_event_time.isoformat()}"
    return "MILS-" + hashlib.sha256(seed.encode()).hexdigest()[:20]


def _idempotency_key(session_id: str, cadence_id: str, decision_cutoff_event_time: datetime) -> str:
    return f"MIL:{session_id}:{cadence_id}:{decision_cutoff_event_time.isoformat()}"


def _minimal_snapshot(
    session_id: str, cadence_id: str, decision_cutoff_event_time: datetime,
    data_quality: DataQualityContext, known_event_risk_state: str,
    active_config_versions: Dict[str, str], timeframe_config_version: str,
    receipt_time: datetime,
) -> MarketIntelligenceSnapshot:
    reasons = (f"MANDATORY_DATA_FAILURE: {data_quality.mandatory_data_failure_reason}",)
    snapshot = MarketIntelligenceSnapshot(
        snapshot_id=_snapshot_id(session_id, cadence_id, decision_cutoff_event_time),
        cadence_id=cadence_id,
        session_id=session_id,
        schema_version=tx.MIL_NEXT_VERSION,
        decision_cutoff_event_time=decision_cutoff_event_time,
        idempotency_key=_idempotency_key(session_id, cadence_id, decision_cutoff_event_time),
        content_hash="",
        active_config_versions=active_config_versions,
        timeframe_config_version=timeframe_config_version,
        data_quality=data_quality,
        event_time_provenance={},
        receipt_time_provenance={"receipt_time": receipt_time.isoformat()},
        timeframe_states=tx.NOT_EVALUATED_DUE_TO_DATA_FAILURE,
        timeframe_agreement=tx.NOT_EVALUATED_DUE_TO_DATA_FAILURE,
        timeframe_agreement_excluded=(),
        composed_regime=tx.NOT_EVALUATED_DUE_TO_DATA_FAILURE,
        primary_thesis=tx.NOT_EVALUATED_DUE_TO_DATA_FAILURE,
        competing_thesis=tx.NOT_EVALUATED_DUE_TO_DATA_FAILURE,
        contradiction_score=tx.NOT_EVALUATED_DUE_TO_DATA_FAILURE,
        oi_context=tx.NOT_EVALUATED_DUE_TO_DATA_FAILURE,
        volatility_posture=tx.NOT_EVALUATED_DUE_TO_DATA_FAILURE,
        liquidity_posture=tx.NOT_EVALUATED_DUE_TO_DATA_FAILURE,
        structural_conflict=tx.NOT_EVALUATED_DUE_TO_DATA_FAILURE,
        known_event_risk_state=known_event_risk_state,
        unscheduled_shock_detected=tx.NOT_EVALUATED_DUE_TO_DATA_FAILURE,
        invalidation_conditions=(),
        posture=tx.POSTURE_NO_TRADE,
        posture_reasons=reasons,
    )
    return _with_hash(snapshot)


def _with_hash(snapshot: MarketIntelligenceSnapshot) -> MarketIntelligenceSnapshot:
    from dataclasses import replace
    content_hash = compute_content_hash(snapshot)
    return replace(snapshot, content_hash=content_hash)


def _derive_contradiction(
    directions: Dict[str, str], primary: TradeThesis, dq: DataQualityContext,
) -> ContradictionScore:
    supporting = sum(1 for d in directions.values() if d == primary.direction)
    contradicting = sum(1 for d in directions.values() if d not in (primary.direction, tx.DIRECTION_UNKNOWN)
                         and d != tx.DIRECTION_NEUTRAL)
    freshness_penalty = 0.0 if dq.freshness == tx.FRESHNESS_FRESH else (
        0.5 if dq.freshness == tx.FRESHNESS_WARNING else 1.0
    )
    regime_penalty = 0.0
    if contradicting >= supporting:
        overall = tx.CONTRADICTION_HIGH
    elif contradicting > 0 or freshness_penalty >= 0.5:
        overall = tx.CONTRADICTION_MODERATE
    else:
        overall = tx.CONTRADICTION_LOW
    return ContradictionScore(
        supporting_domain_count=supporting,
        contradicting_domain_count=contradicting,
        evidence_freshness_penalty=freshness_penalty,
        regime_consistency_penalty=regime_penalty,
        overall=overall,
    )


def _derive_competing_thesis(primary: TradeThesis, directions: Dict[str, str], timeframe_states) -> Optional[object]:
    from .models import CompetingThesis
    opposite = {tx.DIRECTION_BULLISH: tx.DIRECTION_BEARISH, tx.DIRECTION_BEARISH: tx.DIRECTION_BULLISH}
    target = opposite.get(primary.direction)
    if target is None:
        return None
    candidates = [(tf, d) for tf, d in directions.items() if d == target]
    if not candidates:
        return None
    timeframe, _ = candidates[0]
    opposing_thesis = TradeThesis(
        thesis_id=f"COMPETING-{timeframe}",
        direction=target,
        supporting_evidence=(f"{timeframe}_directional_opposition",),
        conviction_rank=1,
        timeframe=timeframe,
    )
    return CompetingThesis(
        thesis=opposing_thesis,
        timeframe=timeframe,
        supporting_evidence=opposing_thesis.supporting_evidence,
        invalidation_condition=f"PRIMARY_THESIS_INVALIDATED_IF_{timeframe}_TREND_CONTINUES",
    )


def _evaluate_posture(
    dq: DataQualityContext, contradiction: ContradictionScore, timeframe_agreement: str,
    known_event_risk_state: str, liquidity_posture: str, unscheduled_shock_detected: bool,
    has_open_positions: bool,
) -> tuple:
    if unscheduled_shock_detected or contradiction.overall == tx.CONTRADICTION_HIGH:
        posture = tx.POSTURE_MANAGE_ONLY if has_open_positions else tx.POSTURE_NO_TRADE
        return posture, (f"RULE_2_INSTABILITY_OR_HIGH_CONTRADICTION",)

    if known_event_risk_state in (tx.KNOWN_EVENT, tx.COVERAGE_UNKNOWN):
        return tx.POSTURE_DEFINED_RISK_ONLY, (f"RULE_3_EVENT_RISK_{known_event_risk_state}",)

    if liquidity_posture == tx.LIQUIDITY_UNKNOWN:
        return tx.POSTURE_REDUCED, ("RULE_4_LIQUIDITY_UNKNOWN_CAP",)

    if timeframe_agreement == tx.TIMEFRAME_AGREEMENT_SPLIT or contradiction.overall == tx.CONTRADICTION_MODERATE:
        return tx.POSTURE_REDUCED, ("RULE_5_TIMEFRAME_SPLIT_OR_MODERATE_CONTRADICTION",)

    return tx.POSTURE_NORMAL, ("RULE_6_NO_CAP_TRIGGERED",)


def build_snapshot(
    session_id: str,
    cadence_id: str,
    decision_cutoff_event_time: datetime,
    market_data_inputs: MarketDataInputs,
    timeframe_config: TimeframeConfig,
    calendar_sources: tuple,
    active_config_versions: Dict[str, str],
    has_open_positions: bool = False,
    clock: Clock = _real_clock,
) -> MarketIntelligenceSnapshot:
    receipt_time = clock()
    points = market_data_inputs.points_by_symbol.get("NIFTY", ())
    source_health = market_data_inputs.source_health.get("underlying_tick")
    max_silence_ms = market_data_inputs.max_silence_ms.get("underlying_tick", 60_000.0)

    dq = build_data_quality_context(points, source_health, max_silence_ms, receipt_time)

    session_date = decision_cutoff_event_time.date()
    calendar_view = load_calendar_view(calendar_sources, session_date)
    known_event_risk_state = resolve_event_risk_state(calendar_view, decision_cutoff_event_time)

    failure_reason = is_mandatory_failure(dq)
    if failure_reason is not None:
        from dataclasses import replace
        dq = replace(dq, mandatory_data_failure_reason=failure_reason)
        return _minimal_snapshot(
            session_id, cadence_id, decision_cutoff_event_time, dq, known_event_risk_state,
            active_config_versions, timeframe_config.config_version, receipt_time,
        )

    timeframe_states = {}
    for tf in timeframe_config.buckets:
        timeframe_states[tf] = fold_timeframe(points, decision_cutoff_event_time, tf, timeframe_config)
    timeframe_agreement, excluded = compute_timeframe_agreement(timeframe_states, timeframe_config)

    directions = {tf: a.direction for tf, a in timeframe_states.items()}
    structural = {tf: a.direction for tf, a in timeframe_states.items()}
    composed_regime = ComposedRegimeView(structural=structural, volatility_instability=tx.REGIME_CALM)

    primary_direction = directions.get(tx.TIMEFRAME_SESSION, tx.DIRECTION_UNKNOWN)
    primary_thesis = TradeThesis(
        thesis_id=f"PRIMARY-{tx.TIMEFRAME_SESSION}",
        direction=primary_direction,
        supporting_evidence=(f"session_direction_{primary_direction}",),
        conviction_rank=2 if primary_direction != tx.DIRECTION_UNKNOWN else 0,
        timeframe=tx.TIMEFRAME_SESSION,
    )
    competing_thesis = _derive_competing_thesis(primary_thesis, directions, timeframe_states)
    contradiction_score = _derive_contradiction(directions, primary_thesis, dq)

    oi_context = OIContext(
        bias="UNKNOWN_POSITIONING", as_of=None, effective_weight=tx.OI_WEIGHT_NONE,
        downgrade_reason="NO_INTRADAY_OI_FEED_WIRED",
    )

    liquidity_posture = tx.LIQUIDITY_UNKNOWN
    volatility_posture = timeframe_states[tx.TIMEFRAME_SESSION]
    structural_conflict = timeframe_agreement == tx.TIMEFRAME_AGREEMENT_SPLIT
    unscheduled_shock_detected = dq.outlier_flag

    posture, posture_reasons = _evaluate_posture(
        dq, contradiction_score, timeframe_agreement, known_event_risk_state,
        liquidity_posture, unscheduled_shock_detected, has_open_positions,
    )

    snapshot = MarketIntelligenceSnapshot(
        snapshot_id=_snapshot_id(session_id, cadence_id, decision_cutoff_event_time),
        cadence_id=cadence_id,
        session_id=session_id,
        schema_version=tx.MIL_NEXT_VERSION,
        decision_cutoff_event_time=decision_cutoff_event_time,
        idempotency_key=_idempotency_key(session_id, cadence_id, decision_cutoff_event_time),
        content_hash="",
        active_config_versions=active_config_versions,
        timeframe_config_version=timeframe_config.config_version,
        data_quality=dq,
        event_time_provenance={"underlying_tick": {"last_event_time": (
            max(p.event_time for p in points).isoformat() if points else None
        )}},
        receipt_time_provenance={"receipt_time": receipt_time.isoformat()},
        timeframe_states=timeframe_states,
        timeframe_agreement=timeframe_agreement,
        timeframe_agreement_excluded=excluded,
        composed_regime=composed_regime,
        primary_thesis=primary_thesis,
        competing_thesis=competing_thesis,
        contradiction_score=contradiction_score,
        oi_context=oi_context,
        volatility_posture=volatility_posture,
        liquidity_posture=liquidity_posture,
        structural_conflict=structural_conflict,
        known_event_risk_state=known_event_risk_state,
        unscheduled_shock_detected=unscheduled_shock_detected,
        invalidation_conditions=(),
        posture=posture,
        posture_reasons=posture_reasons,
    )
    return _with_hash(snapshot)
