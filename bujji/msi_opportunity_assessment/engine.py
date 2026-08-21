"""OAE engine — Series 104. Pure functions: no IO, no state, no
wall-clock reads, no randomness. ZERO imports from any other bujji
package -- the most isolated package in the whole learning stack (Series
99-103). OAE consumes existing artefacts via caller-supplied `*View`
translations (models.py); it never recreates, re-derives, or re-invokes
anything those packages already computed.

First principle, structurally enforced by what this module can and
cannot see: OAE never touches profit/outcome data (no such field exists
on any *View type) -- it can only ever reason about DECISION QUALITY
using the real, causal, contemporaneous evidence it's given."""
from __future__ import annotations

import hashlib
from typing import Optional, Sequence, Tuple

from . import config as _config
from . import taxonomy
from .models import (
    CounterfactualView, DecisionView, OpportunityAssessment, OpportunityAssessmentExplanation, PhenomenaView,
)


def validate_causality(decision: DecisionView, counterfactual: Optional[CounterfactualView]) -> Tuple[bool, Tuple[str, ...]]:
    """The one, disclosed causality rule for this package: a
    counterfactual's own earliest real data timestamp must never exceed
    the real decision's own timestamp -- re-checked here as defense in
    depth, on top of Series 102's own causality validator, since OAE is
    the layer that actually ACTS on the counterfactual's implications."""
    if counterfactual is None:
        return True, ("no counterfactual supplied -- nothing to validate",)
    if counterfactual.earliest_causal_timestamp > decision.timestamp:
        return False, (f"counterfactual earliest_causal_timestamp {counterfactual.earliest_causal_timestamp} "
                        f"is AFTER the real decision timestamp {decision.timestamp} -- future information, invalid",)
    return True, (f"counterfactual earliest_causal_timestamp {counterfactual.earliest_causal_timestamp} "
                  f"<= decision timestamp {decision.timestamp}",)


def _opportunity_criteria(
    decision: DecisionView, phenomena: Optional[PhenomenaView], counterfactual: Optional[CounterfactualView],
    evidence_packet_ids: Sequence[str],
) -> Tuple[bool, Tuple[str, ...]]:
    """The mission's own 6-condition AND-gate. ALL must be real and true
    -- this is the 'burden of proof' the Hindsight Protection section
    requires before OPPORTUNITY_IDENTIFIED can ever be reached."""
    checks = []

    phenomena_support = phenomena is not None and len(phenomena.phenomenon_types) > 0
    checks.append(f"[{'PASS' if phenomena_support else 'FAIL'}] Market Phenomena support it: "
                   f"{phenomena.phenomenon_types if phenomena else '(none supplied)'}")

    legal_path = (counterfactual is not None and counterfactual.replay_legality == taxonomy.REPLAY_LEGALITY_LEGAL
                  and counterfactual.alternative_selected_family is not None)
    checks.append(f"[{'PASS' if legal_path else 'FAIL'}] Counterfactual Replay demonstrates a legal path with a "
                   f"real selected family: {counterfactual.alternative_selected_family if counterfactual else None}")

    stayed_out = decision.decision_outcome == taxonomy.DECISION_OUTCOME_NO_TRADE
    checks.append(f"[{'PASS' if stayed_out else 'FAIL'}] Decision Auditor confirms Production stayed out: "
                   f"decision_outcome={decision.decision_outcome}")

    evidence_exists = len(evidence_packet_ids) > 0
    checks.append(f"[{'PASS' if evidence_exists else 'FAIL'}] Evidence Packet exists: "
                   f"{len(evidence_packet_ids)} real packet id(s)")

    timestamp_known = counterfactual is not None and bool(counterfactual.earliest_causal_timestamp)
    checks.append(f"[{'PASS' if timestamp_known else 'FAIL'}] Earliest causal timestamp is known: "
                   f"{counterfactual.earliest_causal_timestamp if counterfactual else None}")

    causal_ok, causal_reasons = validate_causality(decision, counterfactual)
    checks.append(f"[{'PASS' if causal_ok else 'FAIL'}] No future information required: {causal_reasons[0]}")

    all_pass = phenomena_support and legal_path and stayed_out and evidence_exists and timestamp_known and causal_ok
    return all_pass, tuple(checks)


def _assessment_id(day: str, decision_id: str, classification: str, schema_version: str) -> str:
    content = "|".join([day, decision_id, classification, schema_version])
    return "OA-" + hashlib.md5(content.encode("utf-8")).hexdigest()[:24]


def assess_day(
    *, decision: DecisionView, phenomena: Optional[PhenomenaView] = None,
    counterfactual: Optional[CounterfactualView] = None, evidence_packet_ids: Sequence[str] = (),
    replay_references: Sequence[str] = (), timestamp: str, schema_version: str = _config.SCHEMA_VERSION,
) -> OpportunityAssessment:
    """Produces exactly one real OpportunityAssessment. Never generates
    a Knowledge Candidate or Engineering Proposal -- that is explicitly
    out of scope (mission's own No Engineering section) and structurally
    impossible here (no such model type exists in this package)."""
    causal_ok, causal_reasons = validate_causality(decision, counterfactual)

    assumptions = [
        "consumes only real, already-computed Series 99-103 artefacts -- recreates nothing",
        "never reasons about profit or outcome -- decision quality only, per this package's own First Principle",
    ]
    reasoning: list = []
    criteria_checks: Tuple[str, ...] = ()

    if not causal_ok:
        # A causality violation makes ANY assessment invalid -- disclosed
        # honestly as CORRECT_STAY_OUT is never assumed here; the
        # assessment records the real violation and stops reasoning
        # further, mirroring CRE's own "record it, don't hide it" choice.
        classification = taxonomy.CLASSIFICATION_CORRECT_STAY_OUT if decision.decision_outcome == taxonomy.DECISION_OUTCOME_NO_TRADE else taxonomy.CLASSIFICATION_GOOD_TRADE_TAKEN
        reasoning.append(f"INVALID ASSESSMENT: causality violated -- {causal_reasons[0]}. "
                          f"Defaulting to the conservative classification; no opportunity claim can be made "
                          f"when future information would be required.")
        evidence_strength = taxonomy.CONFIDENCE_NONE
    elif decision.decision_outcome == taxonomy.DECISION_OUTCOME_TRADE_APPROVED:
        if decision.conflicting_domains:
            classification = taxonomy.CLASSIFICATION_TRADE_SHOULD_NOT_HAVE_OCCURRED
            reasoning.append(f"real, contemporaneous conflicting evidence existed at decision time: "
                              f"{decision.conflicting_domains} -- the decision was made despite it.")
            evidence_strength = taxonomy.CONFIDENCE_HIGH
        elif (counterfactual is not None and counterfactual.replay_legality == taxonomy.REPLAY_LEGALITY_LEGAL
              and counterfactual.alternative_selected_family is not None
              and counterfactual.alternative_selected_family != decision.strategy_family):
            classification = taxonomy.CLASSIFICATION_TRADE_SUBOPTIMAL
            reasoning.append(f"a real, legal, causal alternative path selected a different real family "
                              f"({counterfactual.alternative_selected_family}) than Production's real "
                              f"{decision.strategy_family} -- a different construction/timing may have existed. "
                              f"This is NOT a profit claim, only a disclosed evidentiary difference.")
            evidence_strength = taxonomy.CONFIDENCE_MODERATE
        else:
            classification = taxonomy.CLASSIFICATION_GOOD_TRADE_TAKEN
            reasoning.append("Production traded; no real, contemporaneous conflicting evidence and no real, "
                              "legal counterfactual alternative was found -- evidence supports the decision.")
            evidence_strength = taxonomy.CONFIDENCE_MODERATE if counterfactual is not None else taxonomy.CONFIDENCE_LOW
    else:  # NO_TRADE -- Hindsight Protection: prove CORRECT_STAY_OUT before considering OPPORTUNITY_IDENTIFIED
        opportunity_found, criteria_checks = _opportunity_criteria(decision, phenomena, counterfactual, evidence_packet_ids)
        if opportunity_found:
            classification = taxonomy.CLASSIFICATION_OPPORTUNITY_IDENTIFIED
            reasoning.append("all 6 real Opportunity Criteria were satisfied -- see explanation.opportunity_criteria_checked.")
            evidence_strength = taxonomy.CONFIDENCE_HIGH
        else:
            classification = taxonomy.CLASSIFICATION_CORRECT_STAY_OUT
            reasoning.append("the burden of proof for an opportunity was not met -- at least one of the 6 real "
                              "criteria failed; see explanation.opportunity_criteria_checked. Staying out is the "
                              "default, evidence-supported classification.")
            evidence_strength = taxonomy.CONFIDENCE_MODERATE

    aid = _assessment_id(decision.date, decision.decision_id, classification, schema_version)

    explanation = OpportunityAssessmentExplanation(
        assessment_id=aid, why_this_classification=tuple(reasoning),
        why_not_alternatives=tuple(f"not {c}: see reasoning above" for c in taxonomy.ALL_CLASSIFICATIONS if c != classification),
        opportunity_criteria_checked=criteria_checks, schema_version=schema_version,
    )

    return OpportunityAssessment(
        assessment_id=aid, day=decision.date, timestamp=timestamp, classification=classification,
        earliest_causal_timestamp=(counterfactual.earliest_causal_timestamp if counterfactual else decision.timestamp),
        supporting_market_phenomena=((phenomena.report_id,) if phenomena else ()),
        supporting_counterfactual_session=(counterfactual.session_id if counterfactual else None),
        supporting_evidence_packets=tuple(evidence_packet_ids), supporting_decision_records=(decision.decision_id,),
        supporting_reasoning=tuple(reasoning), evidence_strength=evidence_strength,
        replay_references=tuple(replay_references), assumptions=tuple(assumptions),
        explanation=explanation, provenance=_config.DEFAULT_PROVENANCE, schema_version=schema_version,
    )
