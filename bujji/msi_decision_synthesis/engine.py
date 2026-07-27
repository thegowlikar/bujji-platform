"""MSI Decision Synthesis Engine — pure functions, no state, no IO.

Implements Deliverable 5 (Evidence Fusion) and Deliverable 6
(Explainability). Given a tuple of `DomainSignal`s (Deliverable 1's
generic interim input contract) and, optionally, the previous
`MarketOpportunityAssessment`, produces a new, immutable
`MarketOpportunityAssessment` plus its mandatory `Explanation`.

Nothing here recommends a concrete tradeable instrument or any of its
contract-level parameters, sizing, or outcome target. This engine
recommends OPPORTUNITY CLASSES ONLY.

---------------------------------------------------------------------
Agreement / conflict rule (Deliverable 5), stated precisely:
---------------------------------------------------------------------
1. Every `DomainSignal` maps to a "lean" via `config.STATE_LEAN_MAP`:
   OPPORTUNITY_FORMING, NEUTRAL_FORMING, or AMBIGUOUS. Domains listed
   in `taxonomy.PRECONDITION_DOMAINS` (Liquidity, Time Structure) are
   ALWAYS treated as AMBIGUOUS regardless of their `state` string,
   because MSI_V1_FOUNDATION.md Deliverable 2 explicitly defines their
   reasoning responsibility as a structural precondition / weighting
   concern, "not a signal about direction" -- folding them into the
   directional vote would fabricate a read those domains are not
   designed to produce. AMBIGUOUS signals (unmapped states, or
   precondition domains) participate in NEITHER agreement NOR
   conflict; they simply do not vote.
2. Among the remaining ("considered") signals, count OPPORTUNITY_FORMING
   vs. NEUTRAL_FORMING. The lean with strictly more votes is the
   "winning lean". A tie (including 0-0, i.e. zero considered signals)
   resolves to NEUTRAL_FORMING -- the more conservative reading is
   always preferred on a tie, per this project's "no silent data gaps
   / never overclaim" discipline (MSI_V1_FOUNDATION.md Deliverable 10
   Guiding Principle 10).
3. `supporting_domains` = every considered domain whose lean equals the
   winning lean. `conflicting_domains` = every considered domain whose
   lean is the OTHER (non-winning) lean. This is symmetric and exact:
   whenever any considered domain disagrees with the winning lean, it
   is unconditionally placed in `conflicting_domains` -- there is no
   code path that discards or empties this list while a conflict
   genuinely exists (Deliverable 5's "never suppress disagreement").
4. If OPPORTUNITY_FORMING wins, `opportunity_state` is the specific
   opportunity TYPE (DIRECTIONAL/BREAKOUT/VOLATILITY/NEUTRAL/
   MEAN_REVERSION) with the most votes among the OPPORTUNITY_FORMING
   supporting_domains' own preferred types (config.STATE_LEAN_MAP's
   second tuple element); ties broken by `config.TYPE_TIEBREAK_ORDER`
   (declaration order of `taxonomy.OPPORTUNITY_TYPES`).
   If NEUTRAL_FORMING wins and at least one NEUTRAL_FORMING signal
   specifies a preferred type, that type wins the same way; if none
   specify a type (or there were zero considered signals at all),
   `opportunity_state` is `MONITOR` (something to watch, no directional
   evidence either way) unless there are zero signals of any kind at
   all, in which case it is `WAIT`.

---------------------------------------------------------------------
Confidence computation (Deliverable 5): see config.py's constants and
`_confidence_level` below. MUST decrease (never increase) as conflict
increases for a fixed agreement_count -- an explicit, tested property
(`tests/test_msi_decision_synthesis_engine.py::test_confidence_monotonic_with_conflict`).
"""
from __future__ import annotations

import hashlib
from typing import Dict, Optional, Tuple

from . import config as _config
from . import taxonomy
from .models import DomainSignal, Explanation, MarketOpportunityAssessment


# ---------------------------------------------------------------------------
# assessment_id
# ---------------------------------------------------------------------------
def _assessment_id(domain_signals: Tuple[DomainSignal, ...], schema_version: str) -> str:
    """Deterministic content hash over the sorted tuple of domain
    signal contents plus schema_version -- NEVER over timestamp, NEVER
    uuid4(). Sorting by domain_name makes the hash independent of
    caller-supplied ordering. See models.py's module docstring."""
    parts = []
    for sig in sorted(domain_signals, key=lambda s: s.domain_name):
        parts.append(
            "|".join([
                sig.domain_name,
                sig.state,
                f"{sig.confidence:.6f}",
                ",".join(sorted(sig.evidence_ids)),
            ])
        )
    seed = "||".join(parts) + "::" + schema_version
    return "MOA-" + hashlib.md5(seed.encode()).hexdigest()[:24]


# ---------------------------------------------------------------------------
# Lean / agreement / conflict (Deliverable 5)
# ---------------------------------------------------------------------------
def _lean_for_signal(signal: DomainSignal) -> Tuple[str, Optional[str]]:
    if signal.domain_name in taxonomy.PRECONDITION_DOMAINS:
        return _config.LEAN_AMBIGUOUS, None
    return _config.STATE_LEAN_MAP.get(signal.state, (_config.LEAN_AMBIGUOUS, None))


def _considered_signals(domain_signals: Tuple[DomainSignal, ...]) -> Tuple[DomainSignal, ...]:
    return tuple(s for s in domain_signals if _lean_for_signal(s)[0] != _config.LEAN_AMBIGUOUS)


def _winning_lean(considered: Tuple[DomainSignal, ...]) -> str:
    opp_count = sum(1 for s in considered if _lean_for_signal(s)[0] == _config.LEAN_OPPORTUNITY_FORMING)
    neu_count = sum(1 for s in considered if _lean_for_signal(s)[0] == _config.LEAN_NEUTRAL_FORMING)
    if opp_count > neu_count:
        return _config.LEAN_OPPORTUNITY_FORMING
    return _config.LEAN_NEUTRAL_FORMING  # tie (including 0-0) -> conservative default.


def _supporting_and_conflicting(
    considered: Tuple[DomainSignal, ...], winning_lean: str
) -> Tuple[Tuple[str, ...], Tuple[str, ...]]:
    supporting = tuple(sorted(s.domain_name for s in considered if _lean_for_signal(s)[0] == winning_lean))
    conflicting = tuple(sorted(s.domain_name for s in considered if _lean_for_signal(s)[0] != winning_lean))
    return supporting, conflicting


def _resolve_opportunity_state(
    domain_signals: Tuple[DomainSignal, ...],
    considered: Tuple[DomainSignal, ...],
    winning_lean: str,
    supporting_names: Tuple[str, ...],
) -> str:
    if not domain_signals:
        return taxonomy.OPPORTUNITY_STATE_WAIT
    if not considered:
        return taxonomy.OPPORTUNITY_STATE_MONITOR

    by_name = {s.domain_name: s for s in domain_signals}
    type_votes: Dict[str, int] = {}
    for name in supporting_names:
        lean, preferred_type = _lean_for_signal(by_name[name])
        assert lean == winning_lean
        if preferred_type is not None:
            type_votes[preferred_type] = type_votes.get(preferred_type, 0) + 1

    if not type_votes:
        return taxonomy.OPPORTUNITY_STATE_MONITOR

    max_votes = max(type_votes.values())
    tied = {t for t, v in type_votes.items() if v == max_votes}
    for candidate in _config.TYPE_TIEBREAK_ORDER:
        if candidate in tied:
            return candidate
    return taxonomy.OPPORTUNITY_STATE_MONITOR  # unreachable in practice; defensive only.


# ---------------------------------------------------------------------------
# Confidence (Deliverable 5)
# ---------------------------------------------------------------------------
def _confidence_level(agreement_count: int, conflict_count: int, total_considered: int) -> str:
    if total_considered == 0:
        return taxonomy.CONFIDENCE_NONE
    net = agreement_count - conflict_count
    if net >= _config.CONFIDENCE_HIGH_MIN_NET:
        return taxonomy.CONFIDENCE_HIGH
    if net >= _config.CONFIDENCE_HIGH_MIN_NET_WITH_STRONG_AGREEMENT and agreement_count >= _config.CONFIDENCE_HIGH_MIN_AGREEMENT_FOR_NET1:
        return taxonomy.CONFIDENCE_HIGH
    if net >= _config.CONFIDENCE_MODERATE_MIN_NET:
        return taxonomy.CONFIDENCE_MODERATE
    if net >= _config.CONFIDENCE_MODERATE_MIN_NET_AT_ZERO and agreement_count >= 1:
        return taxonomy.CONFIDENCE_MODERATE
    return taxonomy.CONFIDENCE_LOW


# ---------------------------------------------------------------------------
# Opportunity quality (deliberately independent of agreement/conflict)
# ---------------------------------------------------------------------------
def _opportunity_quality(domain_signals: Tuple[DomainSignal, ...]) -> str:
    if not domain_signals:
        return taxonomy.QUALITY_POOR
    coverage = len(domain_signals) / _config.TOTAL_MSI_DOMAINS
    avg_domain_confidence = sum(s.confidence for s in domain_signals) / len(domain_signals)
    score = coverage * avg_domain_confidence
    if score >= _config.QUALITY_EXCELLENT_MIN_SCORE:
        return taxonomy.QUALITY_EXCELLENT
    if score >= _config.QUALITY_GOOD_MIN_SCORE:
        return taxonomy.QUALITY_GOOD
    if score >= _config.QUALITY_FAIR_MIN_SCORE:
        return taxonomy.QUALITY_FAIR
    return taxonomy.QUALITY_POOR


# ---------------------------------------------------------------------------
# Compatibility mapping (Deliverable 4) — a genuine, deterministic,
# disclosed table. See docs/MSI_DECISION_SYNTHESIS_ENGINE.md for the
# full table written out in prose form.
# ---------------------------------------------------------------------------
_COMPATIBILITY_TABLE: Dict[str, Tuple[Tuple[str, ...], Tuple[str, ...]]] = {
    taxonomy.OPPORTUNITY_STATE_DIRECTIONAL: (
        (taxonomy.STRATEGY_FAMILY_DEFINED_RISK_DIRECTIONAL, taxonomy.STRATEGY_FAMILY_HEDGED_DIRECTIONAL, taxonomy.STRATEGY_FAMILY_DIAGONAL),
        (taxonomy.STRATEGY_FAMILY_UNDEFINED_RISK_PREMIUM, taxonomy.STRATEGY_FAMILY_DEFINED_RISK_NEUTRAL),
    ),
    taxonomy.OPPORTUNITY_STATE_BREAKOUT: (
        (taxonomy.STRATEGY_FAMILY_DEFINED_RISK_DIRECTIONAL, taxonomy.STRATEGY_FAMILY_HEDGED_DIRECTIONAL),
        (taxonomy.STRATEGY_FAMILY_UNDEFINED_RISK_PREMIUM, taxonomy.STRATEGY_FAMILY_DEFINED_RISK_NEUTRAL, taxonomy.STRATEGY_FAMILY_CALENDAR),
    ),
    taxonomy.OPPORTUNITY_STATE_VOLATILITY: (
        (taxonomy.STRATEGY_FAMILY_DEFINED_RISK_VOLATILITY, taxonomy.STRATEGY_FAMILY_HEDGED_DIRECTIONAL),
        (taxonomy.STRATEGY_FAMILY_UNDEFINED_RISK_PREMIUM, taxonomy.STRATEGY_FAMILY_CALENDAR, taxonomy.STRATEGY_FAMILY_DEFINED_RISK_NEUTRAL),
    ),
    taxonomy.OPPORTUNITY_STATE_NEUTRAL: (
        (taxonomy.STRATEGY_FAMILY_DEFINED_RISK_NEUTRAL, taxonomy.STRATEGY_FAMILY_UNDEFINED_RISK_PREMIUM, taxonomy.STRATEGY_FAMILY_CALENDAR),
        (taxonomy.STRATEGY_FAMILY_DEFINED_RISK_DIRECTIONAL, taxonomy.STRATEGY_FAMILY_HEDGED_DIRECTIONAL),
    ),
    taxonomy.OPPORTUNITY_STATE_MEAN_REVERSION: (
        (taxonomy.STRATEGY_FAMILY_DEFINED_RISK_NEUTRAL, taxonomy.STRATEGY_FAMILY_CALENDAR, taxonomy.STRATEGY_FAMILY_DIAGONAL),
        (taxonomy.STRATEGY_FAMILY_DEFINED_RISK_DIRECTIONAL, taxonomy.STRATEGY_FAMILY_HEDGED_DIRECTIONAL, taxonomy.STRATEGY_FAMILY_UNDEFINED_RISK_PREMIUM),
    ),
}


def compatible_strategy_families(opportunity_state: str) -> Tuple[Tuple[str, ...], Tuple[str, ...]]:
    """Deliberate, disclosed compatibility/incompatibility mapping
    (Deliverable 4). NO_ACTION/WAIT/MONITOR mean "deploy nothing" --
    every strategy family is incompatible, none are compatible."""
    if opportunity_state in _COMPATIBILITY_TABLE:
        return _COMPATIBILITY_TABLE[opportunity_state]
    # NO_ACTION / WAIT / MONITOR.
    return (), tuple(taxonomy.ALL_STRATEGY_FAMILIES)


# ---------------------------------------------------------------------------
# synthesize (Deliverable 5 — the core fusion function)
# ---------------------------------------------------------------------------
def synthesize(
    domain_signals: Tuple[DomainSignal, ...],
    previous_assessment: Optional[MarketOpportunityAssessment],
    episode_ids: Tuple[str, ...],
    *,
    timestamp: str,
    schema_version: str = _config.SCHEMA_VERSION,
    provenance: str = _config.DEFAULT_PROVENANCE,
) -> MarketOpportunityAssessment:
    considered = _considered_signals(domain_signals)
    winning_lean = _winning_lean(considered)
    supporting, conflicting = _supporting_and_conflicting(considered, winning_lean)
    opportunity_state = _resolve_opportunity_state(domain_signals, considered, winning_lean, supporting)

    confidence_level = _confidence_level(len(supporting), len(conflicting), len(considered))
    opportunity_quality = _opportunity_quality(domain_signals)
    compatible, incompatible = compatible_strategy_families(opportunity_state)

    evidence_ids = tuple(sorted({eid for s in domain_signals for eid in s.evidence_ids}))
    assessment_id = _assessment_id(domain_signals, schema_version)

    return MarketOpportunityAssessment(
        assessment_id=assessment_id,
        timestamp=timestamp,
        opportunity_state=opportunity_state,
        confidence_level=confidence_level,
        opportunity_quality=opportunity_quality,
        supporting_domains=supporting,
        conflicting_domains=conflicting,
        compatible_strategy_families=compatible,
        incompatible_strategy_families=incompatible,
        evidence_ids=evidence_ids,
        episode_ids=tuple(episode_ids),
        provenance=provenance,
        schema_version=schema_version,
    )


# ---------------------------------------------------------------------------
# build_explanation (Deliverable 6 — the 7 mandatory questions,
# genuinely computed from the synthesis inputs/outputs).
# ---------------------------------------------------------------------------
def build_explanation(
    domain_signals: Tuple[DomainSignal, ...],
    assessment: MarketOpportunityAssessment,
    previous_assessment: Optional[MarketOpportunityAssessment],
    *,
    schema_version: str = _config.SCHEMA_VERSION,
) -> Explanation:
    considered = _considered_signals(domain_signals)
    winning_lean = _winning_lean(considered)
    supporting, conflicting = _supporting_and_conflicting(considered, winning_lean)

    # 1. why
    if assessment.opportunity_state in taxonomy.NON_OPPORTUNITY_STATES:
        why = (
            f"opportunity_state={assessment.opportunity_state}: "
            f"{len(supporting)} domain(s) ({', '.join(supporting) or 'none'}) supported the "
            f"winning lean ({winning_lean}) against {len(conflicting)} conflicting domain(s) "
            f"({', '.join(conflicting) or 'none'}); insufficient typed evidence to name a "
            f"specific opportunity type."
        )
    else:
        why = (
            f"opportunity_state={assessment.opportunity_state} was derived from "
            f"{len(supporting)} supporting domain(s) ({', '.join(supporting)}) whose evidence "
            f"({', '.join(sorted({eid for s in domain_signals if s.domain_name in supporting for eid in s.evidence_ids})) or 'none disclosed'}) "
            f"agreed on lean {winning_lean}, against {len(conflicting)} conflicting domain(s) "
            f"({', '.join(conflicting) or 'none'})."
        )

    # 2. why_not — every other opportunity_state considered and rejected.
    why_not = []
    for candidate in taxonomy.ALL_OPPORTUNITY_STATES:
        if candidate == assessment.opportunity_state:
            continue
        why_not.append(
            f"{candidate} rejected: not the winning read (winning lean={winning_lean}, "
            f"{len(supporting)} supporting vs {len(conflicting)} conflicting domain(s))."
        )
    why_not = tuple(why_not)

    # 3. what_changed
    if previous_assessment is None:
        what_changed = None
    elif previous_assessment.assessment_id == assessment.assessment_id:
        what_changed = f"No change from previous assessment {previous_assessment.assessment_id}."
    else:
        changes = []
        if previous_assessment.opportunity_state != assessment.opportunity_state:
            changes.append(f"opportunity_state {previous_assessment.opportunity_state} -> {assessment.opportunity_state}")
        if previous_assessment.confidence_level != assessment.confidence_level:
            changes.append(f"confidence_level {previous_assessment.confidence_level} -> {assessment.confidence_level}")
        if previous_assessment.opportunity_quality != assessment.opportunity_quality:
            changes.append(f"opportunity_quality {previous_assessment.opportunity_quality} -> {assessment.opportunity_quality}")
        if not changes:
            changes.append("no field-level differences from previous assessment despite different assessment_id (input signals changed).")
        what_changed = f"vs previous assessment {previous_assessment.assessment_id}: " + "; ".join(changes)

    # 4/5. domains_agreeing / domains_disagreeing — first-class, directly populated.
    domains_agreeing = supporting
    domains_disagreeing = conflicting

    # 6. missing_evidence — which of the 9 MSI domains did not contribute at all.
    present_domains = {s.domain_name for s in domain_signals}
    missing_evidence = tuple(d for d in taxonomy.ALL_MSI_DOMAINS if d not in present_domains)

    # 7. evidence_that_would_increase_confidence — deterministic, mechanical statements only.
    increase_confidence = []
    if conflicting:
        for domain_name in conflicting:
            increase_confidence.append(f"resolution of conflicting domain {domain_name} to agree with the winning lean")
    if missing_evidence:
        increase_confidence.append(f"one more agreeing domain from: {', '.join(missing_evidence)}")
    if not increase_confidence:
        increase_confidence.append("no further mechanical improvement identified: all 9 MSI domains contributed and none conflict")
    evidence_that_would_increase_confidence = tuple(increase_confidence)

    return Explanation(
        assessment_id=assessment.assessment_id,
        why=why,
        why_not=why_not,
        what_changed=what_changed,
        domains_agreeing=domains_agreeing,
        domains_disagreeing=domains_disagreeing,
        missing_evidence=missing_evidence,
        evidence_that_would_increase_confidence=evidence_that_would_increase_confidence,
        schema_version=schema_version,
    )
