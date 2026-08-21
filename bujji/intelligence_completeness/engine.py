"""Intelligence Completeness Engine — pure functions, no state, no IO,
no wall-clock reads. Phase 9, Upgrade 2.

Reads ONE already-persisted `intelligence_cycle.jsonl` record (a plain
dict, exactly as `IntelligenceCycleRecorder.record_cycle()` already
produces and already serializes -- no new object, no new broker call)
and answers, per tracked domain: "how complete is Bujji's understanding
here, right now?"

DESIGN PHILOSOPHY (explicit, non-negotiable, restated from the mission
brief this module was built against): a domain that honestly reports
UNKNOWN, with a disclosed reason, must never score worse than a domain
that reports a confident read on thin evidence. This module never
invents evidence, never upgrades a domain's own reported confidence,
and never reads whether a trade was selected -- `completeness_score`
and `honesty_score` are both pure functions of the domains' OWN
disclosed confidence/contradiction fields, nothing else.

Not part of the trading path. Nothing here is imported by
ShadowSessionRunner, IntelligenceCycleRecorder, or any MSI engine --
this package only ever reads records those already wrote.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, Optional, Tuple

from .models import (
    STATUS_COMPLETE, STATUS_PARTIAL, STATUS_UNKNOWN,
    CycleCompletenessReport, DomainCompleteness,
)

# NONE/LOW/MODERATE/HIGH is the established confidence-level convention
# used throughout this codebase's MSI engines (see e.g.
# msi_strategy_selection_foundation.taxonomy.ALL_CONFIDENCE_LEVELS).
_STRING_CONFIDENCE_STATUS = {
    "NONE": STATUS_UNKNOWN,
    "LOW": STATUS_PARTIAL,
    "MODERATE": STATUS_PARTIAL,
    "HIGH": STATUS_COMPLETE,
}

# msi_consensus.taxonomy.CONSENSUS_LEVEL_RANK's own vocabulary.
_CONSENSUS_LEVEL_STATUS = {
    "NO_CONSENSUS": STATUS_UNKNOWN,
    "WEAK_CONSENSUS": STATUS_PARTIAL,
    "MODERATE_CONSENSUS": STATUS_PARTIAL,
    "STRONG_CONSENSUS": STATUS_COMPLETE,
    "UNANIMOUS_CONSENSUS": STATUS_COMPLETE,
}

# bujji.intelligence.models.SpreadTightness's own vocabulary.
_LIQUIDITY_TIGHTNESS_STATUS = {
    "UNKNOWN": STATUS_UNKNOWN,
    "WIDE": STATUS_PARTIAL,
    "NORMAL": STATUS_PARTIAL,
    "TIGHT": STATUS_COMPLETE,
}

# msi_participant_positioning.taxonomy's own vocabulary (UNKNOWN/WEAK/
# MODERATE/STRONG) -- deliberately distinct from the NONE/LOW/MODERATE/
# HIGH convention used elsewhere; conflating the two would have silently
# misclassified every real WEAK/STRONG reading as UNKNOWN (caught during
# this module's own spot-check against a real live cycle record).
_POSITIONING_STRENGTH_STATUS = {
    "UNKNOWN": STATUS_UNKNOWN,
    "WEAK": STATUS_PARTIAL,
    "MODERATE": STATUS_PARTIAL,
    "STRONG": STATUS_COMPLETE,
}


def _status_from_string_confidence(value: Optional[str], vocabulary: Dict[str, str] = _STRING_CONFIDENCE_STATUS) -> Tuple[str, str]:
    if value is None:
        return STATUS_UNKNOWN, "no confidence value present"
    status = vocabulary.get(value)
    if status is None:
        return STATUS_UNKNOWN, f"unrecognized confidence value {value!r}"
    return status, f"confidence={value}"


def _eval_simple_confidence_domain(
    domain_name: str, confidence_field: str, vocabulary: Dict[str, str] = _STRING_CONFIDENCE_STATUS,
) -> Callable[[Optional[dict]], DomainCompleteness]:
    def _eval(value: Optional[dict]) -> DomainCompleteness:
        if not isinstance(value, dict):
            reason = "no assessment produced this cycle" if value is None else f"unexpected non-dict value for {domain_name}"
            return DomainCompleteness(domain_name, STATUS_UNKNOWN, reason, None)
        raw = value.get(confidence_field)
        status, reason = _status_from_string_confidence(raw, vocabulary)
        return DomainCompleteness(domain_name, status, reason, raw)
    return _eval


def _eval_consensus(value: Optional[dict]) -> DomainCompleteness:
    if not isinstance(value, dict):
        reason = "no assessment produced this cycle" if value is None else "unexpected non-dict value for consensus"
        return DomainCompleteness("consensus", STATUS_UNKNOWN, reason, None)
    level = value.get("consensus_level")
    if level is None:
        return DomainCompleteness("consensus", STATUS_UNKNOWN, "no consensus_level present", None)
    status = _CONSENSUS_LEVEL_STATUS.get(level, STATUS_UNKNOWN)
    reason = f"consensus_level={level}" if level in _CONSENSUS_LEVEL_STATUS else f"unrecognized consensus_level {level!r}"
    return DomainCompleteness("consensus", status, reason, level)


def _eval_liquidity(value: Optional[dict]) -> DomainCompleteness:
    if not isinstance(value, dict):
        reason = "no liquidity reading produced this cycle" if value is None else "unexpected non-dict value for liquidity"
        return DomainCompleteness("liquidity", STATUS_UNKNOWN, reason, None)
    tightness = value.get("tightness")
    if tightness is None:
        return DomainCompleteness("liquidity", STATUS_UNKNOWN, "no tightness present", None)
    status = _LIQUIDITY_TIGHTNESS_STATUS.get(tightness, STATUS_UNKNOWN)
    reason = value.get("reason") or f"tightness={tightness}"
    return DomainCompleteness("liquidity", status, reason, tightness)


# Domain name -> (record key, evaluator). Declarative, mirrors the
# STRATEGY_DEFINITIONS table pattern already established in
# msi_strategy_selection_foundation.taxonomy -- one row per tracked
# domain, no per-domain branching outside this table.
_DOMAIN_EVALUATORS: Dict[str, Tuple[str, Callable[[Optional[dict]], DomainCompleteness]]] = {
    "price_structure": ("price_structure", _eval_simple_confidence_domain("price_structure", "confidence")),
    "market_structure": ("market_structure", _eval_simple_confidence_domain("market_structure", "confidence")),
    "participant_positioning": ("participant_positioning", _eval_simple_confidence_domain("participant_positioning", "positioning_strength", _POSITIONING_STRENGTH_STATUS)),
    "volatility_structure": ("volatility_structure", _eval_simple_confidence_domain("volatility_structure", "confidence")),
    "liquidity": ("liquidity", _eval_liquidity),
    "consensus": ("consensus", _eval_consensus),
    "opportunity": ("opportunity", _eval_simple_confidence_domain("opportunity", "confidence_level")),
    "strategy_eligibility": ("strategy_eligibility", _eval_simple_confidence_domain("strategy_eligibility", "eligibility_confidence")),
    "strategy_selection": ("strategy_selection", _eval_simple_confidence_domain("strategy_selection", "confidence")),
}

ALL_TRACKED_DOMAINS: Tuple[str, ...] = tuple(_DOMAIN_EVALUATORS.keys())

_STATUS_WEIGHT = {STATUS_COMPLETE: 1.0, STATUS_PARTIAL: 0.5, STATUS_UNKNOWN: 0.0}

# Fields on the record's own assessments that disclose an internal
# contradiction, per domain -- checked ONLY to detect the one real
# dishonesty pattern this module penalizes: a domain claiming COMPLETE
# understanding while its own engine also disclosed a contradiction.
# An UNKNOWN or PARTIAL domain is never penalized for a contradiction --
# disclosing uncertainty honestly is never the problem this guards
# against.
_CONTRADICTION_FIELDS = {
    "market_structure": "contradictions",
    "consensus": "conflicting_domains",
    "strategy_eligibility": "contradictions",
}


def _has_disclosed_contradiction(record: dict, domain_name: str) -> bool:
    field_name = _CONTRADICTION_FIELDS.get(domain_name)
    if field_name is None:
        return False
    value = record.get(domain_name)
    if not isinstance(value, dict):
        return False
    contradictions = value.get(field_name)
    return bool(contradictions)


def evaluate_cycle(record: dict) -> CycleCompletenessReport:
    """Pure function: one `intelligence_cycle.jsonl` record (already
    deserialized) -> one CycleCompletenessReport. Never raises on a
    missing/None domain -- an absent domain is itself honestly UNKNOWN,
    never a crash."""
    domains = tuple(
        evaluator(record.get(record_key)) for record_key, evaluator in _DOMAIN_EVALUATORS.values()
    )

    completeness_score = round(
        100.0 * sum(_STATUS_WEIGHT[d.status] for d in domains) / len(domains), 2
    ) if domains else 0.0

    # Honesty score starts at 100 (every domain here is, by
    # construction, reporting its OWN real engine's disclosed
    # confidence -- never fabricated) and is only reduced for the one
    # detectable dishonesty pattern: claiming COMPLETE while the
    # domain's own assessment also disclosed a contradiction.
    penalty_per_violation = 100.0 / len(domains) if domains else 0.0
    violations = sum(
        1 for d in domains
        if d.status == STATUS_COMPLETE and _has_disclosed_contradiction(record, d.domain)
    )
    honesty_score = round(max(0.0, 100.0 - penalty_per_violation * violations), 2)

    return CycleCompletenessReport(
        timestamp=record.get("timestamp", ""),
        domains=domains,
        completeness_score=completeness_score,
        honesty_score=honesty_score,
    )


def evaluate_session(records) -> Tuple[CycleCompletenessReport, ...]:
    """Maps evaluate_cycle over an iterable of records (e.g. every line
    of a session's intelligence_cycle.jsonl, already parsed). Pure map,
    never a comparison/ranking/trend judgment -- trend analysis is left
    to the caller reading the returned tuple."""
    return tuple(evaluate_cycle(r) for r in records)


# ---------------------------------------------------------------------------
# Phase 11 Upgrade 4 -- Intelligence Completeness Expansion. Four NEW,
# purely ADDITIVE dimensions layered on top of evaluate_cycle()'s
# original 9 MSI domains. evaluate_cycle() itself is untouched --
# existing callers/tests see identical behavior. These new dimensions
# read the SAME optional inputs Phase 11's other new modules already
# produce (memory_health, regime_memory, narrative) -- never fabricated
# when those inputs are absent (honestly UNKNOWN instead).
# ---------------------------------------------------------------------------
def _eval_memory_completeness(memory_health: Optional[dict]) -> DomainCompleteness:
    """"Do we have enough history?" -- read from Phase 10's
    memory_health telemetry, never re-derived or guessed here."""
    if not isinstance(memory_health, dict):
        return DomainCompleteness("memory_completeness", STATUS_UNKNOWN, "no memory_health telemetry available", None)
    available = memory_health.get("historical_events_available")
    unresolved = memory_health.get("unresolved_event_references")
    if not available:
        return DomainCompleteness("memory_completeness", STATUS_UNKNOWN, "no accumulated history yet this session", available)
    if unresolved:
        return DomainCompleteness(
            "memory_completeness", STATUS_PARTIAL,
            f"{unresolved} historical event reference(s) unresolved", memory_health.get("history_resolution_ratio"),
        )
    return DomainCompleteness(
        "memory_completeness", STATUS_COMPLETE,
        f"{available} historical events available, all references resolved", memory_health.get("history_resolution_ratio"),
    )


def _eval_regime_completeness(regime_memory: Optional[dict]) -> DomainCompleteness:
    """"Has the current regime persisted long enough to trust it?" --
    read from Phase 11 Upgrade 1's regime-memory stability read."""
    if not isinstance(regime_memory, dict):
        return DomainCompleteness("regime_completeness", STATUS_UNKNOWN, "no regime_memory available", None)
    stability = regime_memory.get("stability")
    if stability in (None, "INSUFFICIENT_HISTORY"):
        return DomainCompleteness("regime_completeness", STATUS_UNKNOWN, "insufficient regime history this session", stability)
    if stability == "FRAGILE":
        return DomainCompleteness("regime_completeness", STATUS_PARTIAL, "regime just changed -- too early to assess persistence", stability)
    if stability == "WEAKENING":
        return DomainCompleteness("regime_completeness", STATUS_PARTIAL, "current regime duration below recent session norm", stability)
    if stability == "STABLE":
        return DomainCompleteness("regime_completeness", STATUS_COMPLETE, "current regime has persisted at or above the recent session norm", stability)
    return DomainCompleteness("regime_completeness", STATUS_UNKNOWN, f"unrecognized stability value {stability!r}", stability)


def _eval_narrative_completeness(narrative: Optional[dict]) -> DomainCompleteness:
    """"Are major domains aligned?" -- read from Phase 11 Upgrade 2's
    narrative confidence, downgraded if the narrative itself disclosed
    a contradiction (claiming alignment while flagging one is exactly
    the dishonesty pattern this engine already guards against)."""
    if not isinstance(narrative, dict):
        return DomainCompleteness("narrative_completeness", STATUS_UNKNOWN, "no narrative available", None)
    confidence = narrative.get("confidence")
    status, reason = _status_from_string_confidence(confidence)
    contradictions = narrative.get("contradictions") or []
    if status == STATUS_COMPLETE and contradictions:
        status = STATUS_PARTIAL
        reason = f"{reason}, but {len(contradictions)} contradiction(s) disclosed"
    return DomainCompleteness("narrative_completeness", status, reason, confidence)


def _eval_data_quality_completeness(record: dict) -> DomainCompleteness:
    """"Are option quotes complete? Is the liquidity reading reliable?"
    -- read from this cycle's own snapshot health/liquidity data_quality
    fields, never re-derived."""
    snapshot_health = record.get("market_snapshot_health")
    missing_fields = record.get("market_snapshot_missing_fields") or []
    liquidity = record.get("liquidity")
    liquidity_quality = liquidity.get("data_quality") if isinstance(liquidity, dict) else None

    if snapshot_health is None and liquidity_quality is None:
        return DomainCompleteness("data_quality_completeness", STATUS_UNKNOWN, "no snapshot health or liquidity data_quality present", None)

    issues = []
    if snapshot_health not in (None, "OK"):
        issues.append(f"snapshot_health={snapshot_health}")
    if missing_fields:
        issues.append(f"missing_fields={list(missing_fields)}")
    if liquidity_quality == "INSUFFICIENT":
        issues.append("liquidity data_quality=INSUFFICIENT")

    if snapshot_health == "OK" and not missing_fields and liquidity_quality in (None, "SUFFICIENT"):
        return DomainCompleteness("data_quality_completeness", STATUS_COMPLETE, "snapshot healthy, no missing fields, liquidity reliable", snapshot_health)
    if snapshot_health == "UNAVAILABLE":
        return DomainCompleteness("data_quality_completeness", STATUS_UNKNOWN, "; ".join(issues) or "snapshot unavailable", snapshot_health)
    return DomainCompleteness("data_quality_completeness", STATUS_PARTIAL, "; ".join(issues) or "partial data quality", snapshot_health)


EXPANDED_DOMAINS: Tuple[str, ...] = (
    "memory_completeness", "regime_completeness", "narrative_completeness", "data_quality_completeness",
)


def evaluate_cycle_extended(
    record: dict,
    memory_health: Optional[dict] = None,
    regime_memory: Optional[dict] = None,
    narrative: Optional[dict] = None,
) -> CycleCompletenessReport:
    """evaluate_cycle()'s original 9 domains, PLUS the 4 new Phase 11
    dimensions above. All three new inputs are optional and independent
    of `record` -- passing none of them reproduces evaluate_cycle()'s
    original 9-domain score exactly, just with 4 extra honestly-UNKNOWN
    domains factored into the (now 13-domain) average. Never mutates or
    re-derives anything evaluate_cycle() already computed."""
    base = evaluate_cycle(record)

    new_domains = (
        _eval_memory_completeness(memory_health),
        _eval_regime_completeness(regime_memory),
        _eval_narrative_completeness(narrative),
        _eval_data_quality_completeness(record),
    )
    all_domains = base.domains + new_domains

    completeness_score = round(100.0 * sum(_STATUS_WEIGHT[d.status] for d in all_domains) / len(all_domains), 2)

    # Honesty score: same violation rule as evaluate_cycle(), extended
    # to the new domains -- narrative_completeness is the only new
    # domain with its own contradiction-shaped signal (already folded
    # into its status above, so no double-penalty needed here); the
    # other three have no separate "disclosed contradiction" field to
    # check, so they simply never trigger a violation.
    penalty_per_violation = 100.0 / len(all_domains)
    violations = sum(
        1 for d in base.domains
        if d.status == STATUS_COMPLETE and _has_disclosed_contradiction(record, d.domain)
    )
    honesty_score = round(max(0.0, 100.0 - penalty_per_violation * violations), 2)

    return CycleCompletenessReport(
        timestamp=record.get("timestamp", ""),
        domains=all_domains,
        completeness_score=completeness_score,
        honesty_score=honesty_score,
    )
