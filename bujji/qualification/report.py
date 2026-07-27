"""Qualification Report — BUJJI Options OS v3, Engineering Series 58.

Deterministic summary statistics over a `QualificationRecorder`'s
records -- counts only, never P&L, never a strategy-performance
verdict. `build_report()` is a pure function of the records it is
given: the same records, in the same order, always produce a
byte-identical report.

Naming note, disclosed rather than silently worked around: a
different, single-scenario-focused `QualificationReport` already
exists at `bujji.qualification.replay_models.QualificationReport`
(Engineering Series 46). That class summarizes one `ReplayScenario`
run through the deterministic pipeline in isolation; this module's
`QualificationReport` summarizes many replay sessions run through the
full integrated runtime (composition root, health, circuit breaker,
rate limiter, Shadow Runtime). The two are never imported into the
same namespace under the same name, and neither imports the other --
see `docs/HISTORICAL_QUALIFICATION_FRAMEWORK.md` for the full
disclosure.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, Optional, Tuple

from .recorder import (
    RUNTIME_OUTCOME_COMPLETED,
    RUNTIME_OUTCOME_FAILED,
    RUNTIME_OUTCOME_REJECTED_CIRCUIT,
    RUNTIME_OUTCOME_REJECTED_RATE_LIMIT,
    QualificationRecord,
)

Clock = Callable[[], datetime]


def _real_clock() -> datetime:
    return datetime.now()


@dataclass(frozen=True)
class QualificationReport:
    report_id: str
    total_replay_sessions: int
    completed_runs: int
    rejected_runs: int
    runtime_failures: int
    health_state_counts: Dict[str, int]
    circuit_state_counts: Dict[str, int]
    rate_limit_state_counts: Dict[str, int]
    insufficient_data_occurrences: int
    replay_identifiers: Tuple[str, ...]
    qualification_fingerprints: Tuple[str, ...]
    timestamp: str
    version: str = "1.0.0"
    # Series 69 Phase 2 (Qualification Observability Expansion,
    # recording-only): per-session detail the Observatory
    # (`bujji.observatory.comparison.CANONICAL_FIELD_ORDER`) already
    # expects up to 9 keys of, but which `build_report()` never
    # produced before -- only whole-corpus aggregates existed. Defaults
    # to `()` so every existing caller of `QualificationReport(...)`
    # that does not pass `sessions` is unaffected (see
    # `bujji.qualification.replay_models` naming-disclosure note above
    # -- that is a different, unrelated `QualificationReport` class).
    sessions: Tuple[Dict[str, Optional[Any]], ...] = field(default_factory=tuple)


def _report_id(replay_identifiers: Tuple[str, ...], timestamp: str) -> str:
    seed = f"QREPORT|{'|'.join(replay_identifiers)}|{timestamp}"
    return "QREPORT-" + hashlib.md5(seed.encode()).hexdigest()[:16]


def _session_entry(r: QualificationRecord) -> Dict[str, Optional[Any]]:
    """Build one Observatory-shaped session entry from an already
    -recorded `QualificationRecord`. Every value here is read verbatim
    off objects the pipeline already produced (`r.scenario`,
    `r.strategy_decision`, `r.market_state_assessment`) -- nothing is
    derived, inferred, or defaulted to anything other than `None` when
    genuinely absent. `getattr(..., None)` is used defensively since
    both `scenario` and `market_state_assessment` are optional fields
    added in Series 69 Phase 2 and may be `None` on records built
    before that change (backward compatible).
    """
    scenario = getattr(r, "scenario", None)
    msa = getattr(r, "market_state_assessment", None)
    strategy_decision = r.strategy_decision
    risk_decision = getattr(r, "risk_decision", None)
    capital_decision = getattr(r, "capital_decision", None)
    execution_plan = getattr(r, "execution_plan", None)
    evidence_interpretation = getattr(r, "evidence_interpretation", None)
    ontology_snapshot = getattr(evidence_interpretation, "ontology_snapshot", None) if evidence_interpretation else None
    # Series 70 Phase 2 (Context Stability Observatory, recording-only):
    # `published_state`, when present, is the `PublishedState` this
    # session was originally flattened from (see
    # historical_runner.py::run_corpus() and recorder.py::QualificationRecord
    # -- Series 69's own `getattr(..., None)` defensive pattern, since
    # `published_state` is an optional field that may be `None` on
    # records built before this sprint or without published_states
    # supplied to run_corpus()).
    published_state = getattr(r, "published_state", None)

    return {
        "id": r.replay_identifier,
        "outcome": r.runtime_outcome.status,
        "strategy": getattr(strategy_decision, "selected_strategy", None) if strategy_decision else None,
        "market_context": getattr(scenario, "market_context", None) if scenario else None,
        "market_opinion": getattr(scenario, "market_opinion", None) if scenario else None,
        "context_stability": getattr(scenario, "context_stability", None) if scenario else None,
        "calibration": getattr(scenario, "calibration", None) if scenario else None,
        "governance": getattr(scenario, "governance", None) if scenario else None,
        "lifecycle": getattr(scenario, "lifecycle", None) if scenario else None,
        "contract": getattr(scenario, "contract", None) if scenario else None,
        "market_character": getattr(msa, "market_character", None) if msa else None,
        "market_phase": getattr(msa, "market_phase", None) if msa else None,
        "confidence": getattr(msa, "confidence", None) if msa else None,
        # Series 69 Phase 2b (deep pass, recording-only): all values
        # below are read verbatim off objects the pipeline already
        # produced (r.risk_decision, r.capital_decision,
        # r.execution_plan, r.evidence_interpretation) -- nothing
        # derived, nothing inferred, None when genuinely absent.
        "selection_status": getattr(strategy_decision, "selection_status", None) if strategy_decision else None,
        "selection_confidence": getattr(strategy_decision, "selection_confidence", None) if strategy_decision else None,
        "selection_reason": getattr(strategy_decision, "selection_reason", None) if strategy_decision else None,
        "risk_status": getattr(risk_decision, "status", None) if risk_decision else None,
        "risk_level": getattr(risk_decision, "risk_level", None) if risk_decision else None,
        "risk_approval": getattr(risk_decision, "approval", None) if risk_decision else None,
        "risk_blocking_reason": getattr(risk_decision, "blocking_reason", None) if risk_decision else None,
        "capital_intent": getattr(capital_decision, "capital_intent", None) if capital_decision else None,
        "capital_allocation_status": getattr(capital_decision, "allocation_status", None) if capital_decision else None,
        "capital_allocation_reason": getattr(capital_decision, "allocation_reason", None) if capital_decision else None,
        "execution_status": getattr(execution_plan, "status", None) if execution_plan else None,
        "execution_intent": getattr(execution_plan, "execution_intent", None) if execution_plan else None,
        "evidence_opportunity_state": getattr(ontology_snapshot, "opportunity_state", None) if ontology_snapshot else None,
        "evidence_risk_state": getattr(ontology_snapshot, "risk_state", None) if ontology_snapshot else None,
        # Series 70 Phase 2: whole-replay ContextStability detail, the
        # other five MarketContext dimensions, and the compact
        # per-cycle memory-transition summary -- all read verbatim off
        # `published_state` (see PublishedState in publication_replay.py),
        # never derived, never inferred, `None`/`()` when absent.
        "stability_reasoning_summary": getattr(published_state, "stability_reasoning_summary", None) if published_state else None,
        "stability_transition_count": getattr(published_state, "stability_transition_count", None) if published_state else None,
        "stability_persistence_length": getattr(published_state, "stability_persistence_length", None) if published_state else None,
        "stability_dimension_agreement": getattr(published_state, "stability_dimension_agreement", None) if published_state else None,
        "stability_confidence_variance": getattr(published_state, "stability_confidence_variance", None) if published_state else None,
        "stability_context_lifetime": getattr(published_state, "stability_context_lifetime", None) if published_state else None,
        "context_volatility": getattr(published_state, "context_volatility", None) if published_state else None,
        "context_liquidity": getattr(published_state, "context_liquidity", None) if published_state else None,
        "context_regime": getattr(published_state, "context_regime", None) if published_state else None,
        "context_conviction": getattr(published_state, "context_conviction", None) if published_state else None,
        "context_stability_dimension": getattr(published_state, "context_stability_dimension", None) if published_state else None,
        "transition_events": getattr(published_state, "transition_events", None) if published_state else None,
    }


def build_report(records: Tuple[QualificationRecord, ...], clock: Clock = _real_clock) -> QualificationReport:
    """Build a deterministic report from a recorder's records.

    Never computes profitability, never scores strategy quality --
    only counts already-recorded, already-classified evidence.
    """
    timestamp = clock().isoformat()

    total = len(records)
    completed = sum(1 for r in records if r.runtime_outcome.status == RUNTIME_OUTCOME_COMPLETED)
    rejected = sum(
        1
        for r in records
        if r.runtime_outcome.status in (RUNTIME_OUTCOME_REJECTED_CIRCUIT, RUNTIME_OUTCOME_REJECTED_RATE_LIMIT)
    )
    failures = sum(1 for r in records if r.runtime_outcome.status == RUNTIME_OUTCOME_FAILED)

    health_state_counts: Dict[str, int] = {}
    circuit_state_counts: Dict[str, int] = {}
    rate_limit_state_counts: Dict[str, int] = {}
    insufficient_data_occurrences = 0

    for r in records:
        health_state_counts[r.health_snapshot.overall] = health_state_counts.get(r.health_snapshot.overall, 0) + 1
        circuit_state_counts[r.circuit_decision.state] = circuit_state_counts.get(r.circuit_decision.state, 0) + 1
        rate_limit_state_counts[r.rate_limit_decision.state] = (
            rate_limit_state_counts.get(r.rate_limit_decision.state, 0) + 1
        )
        if r.health_snapshot.overall == "INSUFFICIENT_DATA":
            insufficient_data_occurrences += 1
        if r.circuit_decision.state == "INSUFFICIENT_DATA":
            insufficient_data_occurrences += 1
        if r.rate_limit_decision.state == "INSUFFICIENT_DATA":
            insufficient_data_occurrences += 1

    replay_identifiers = tuple(r.replay_identifier for r in records)
    qualification_fingerprints = tuple(r.qualification_fingerprint for r in records)
    sessions = tuple(_session_entry(r) for r in records)

    return QualificationReport(
        report_id=_report_id(replay_identifiers, timestamp),
        total_replay_sessions=total,
        completed_runs=completed,
        rejected_runs=rejected,
        runtime_failures=failures,
        health_state_counts=health_state_counts,
        circuit_state_counts=circuit_state_counts,
        rate_limit_state_counts=rate_limit_state_counts,
        insufficient_data_occurrences=insufficient_data_occurrences,
        replay_identifiers=replay_identifiers,
        qualification_fingerprints=qualification_fingerprints,
        timestamp=timestamp,
        version="1.0.0",
        sessions=sessions,
    )
