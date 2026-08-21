"""EEB engine — Series 106. Pure functions: no IO, no state, no
wall-clock reads, no randomness. ZERO imports from any other bujji
package -- same isolation discipline as Series 104/105, and for the same
reason: EEB only reviews caller-supplied, real, already-computed
evidence; it never re-derives, re-invokes, or fabricates anything.

First principle, structurally enforced by what this module can and
cannot produce: there is no code-generation, parameter-tuning, or
implementation-instruction field anywhere in models.py, so
READY_FOR_ENGINEERING_REVIEW can never carry more meaning than 'design a
controlled proposal' -- the strongest thing this module can ever say."""
from __future__ import annotations

import hashlib
from typing import Sequence, Tuple

from . import config as _config
from . import taxonomy
from .models import (
    EngineeringEvidenceExplanation, EngineeringEvidenceReport, KnowledgeValidationView, OpportunityAssessmentRef,
)

_DEFAULT_LIMITATION = (
    "no additional limitations disclosed by the caller -- this review is scoped entirely to the "
    "real Series 99-105 learning-stack artefacts supplied; it does not itself observe live market conditions"
)


def _report_id(hypothesis_label: str, validation_id: str, decision: str, schema_version: str) -> str:
    content = "|".join([hypothesis_label, validation_id, decision, schema_version])
    return "EEB-" + hashlib.md5(content.encode("utf-8")).hexdigest()[:24]


def review_evidence(
    *, validation: KnowledgeValidationView, opportunity_assessments: Sequence[OpportunityAssessmentRef] = (),
    evidence_packet_ids: Sequence[str] = (), counterfactual_session_ids: Sequence[str] = (),
    phenomena_report_ids: Sequence[str] = (), decision_record_ids: Sequence[str] = (),
    contradictory_observations: Sequence[str] = (), known_limitations: Sequence[str] = (),
    generated_timestamp: str, schema_version: str = _config.SCHEMA_VERSION,
) -> EngineeringEvidenceReport:
    """Produces exactly one real EngineeringEvidenceReport. Always
    computes one of the three 'live' decisions
    (INSUFFICIENT_EVIDENCE/CONTINUE_OBSERVING/READY_FOR_ENGINEERING_
    REVIEW) -- ARCHIVED and SUPERSEDED are never assigned here (see
    archive() and query.py respectively)."""
    criteria: list = []

    # 1. Evidence strength / trend / diversity / causal validity / replay
    # reproducibility -- all real, already synthesised into
    # validation.validation_state by Series 105; re-cited individually
    # here for a fully disclosed, auditable trail rather than a single
    # opaque pass-through.
    criteria.append(f"evidence_strength (validation_state): {validation.validation_state}")
    criteria.append(f"replay_reproducibility (replay_support_ratio): {validation.replay_support_ratio:.2f}")
    criteria.append(f"diversity_of_market_conditions (diversity_count): {validation.diversity_count}")
    criteria.append(f"causal_validity (causal_validity_ratio): {validation.causal_validity_ratio:.2f}")
    criteria.append(f"evidence_trend (growth/decay): {validation.evidence_growth}/{validation.evidence_decay}")

    positive_opportunities = [o for o in opportunity_assessments if o.classification in taxonomy.OPPORTUNITY_POSITIVE_CLASSIFICATIONS]
    opportunity_quality_ok = len(positive_opportunities) > 0
    criteria.append(f"opportunity_quality: {len(positive_opportunities)}/{len(opportunity_assessments)} "
                     f"real referenced Opportunity Assessment(s) are positive "
                     f"({', '.join(taxonomy.OPPORTUNITY_POSITIVE_CLASSIFICATIONS)})")

    has_contradictions = len(contradictory_observations) > 0
    criteria.append(f"contradictory_evidence: {len(contradictory_observations)} real, disclosed "
                     f"contradicting observation(s)" if has_contradictions else "contradictory_evidence: none disclosed")

    limitations = tuple(known_limitations) if known_limitations else (_DEFAULT_LIMITATION,)
    criteria.append(f"known_limitations: {len(limitations)} disclosed")

    reasoning: list = []

    if validation.validation_state in (
        taxonomy.VALIDATION_STATE_NOT_OBSERVED, taxonomy.VALIDATION_STATE_OBSERVED,
        taxonomy.VALIDATION_STATE_REPEATED, taxonomy.VALIDATION_STATE_INVALIDATED,
    ):
        decision = taxonomy.DECISION_INSUFFICIENT_EVIDENCE
        reasoning.append(f"validation_state={validation.validation_state} does not clear the real evidentiary bar "
                          f"-- either too few real occurrences or the pattern is directly contradicted by its own "
                          f"majority evidence (Series 105).")
    elif has_contradictions:
        decision = taxonomy.DECISION_CONTINUE_OBSERVING
        reasoning.append(f"{len(contradictory_observations)} real, disclosed contradicting observation(s) exist "
                          f"beyond what Series 105's own validation already measured -- continue observing before "
                          f"a formal engineering review is justified.")
    elif validation.validation_state == taxonomy.VALIDATION_STATE_DECAYING:
        decision = taxonomy.DECISION_CONTINUE_OBSERVING
        reasoning.append("validation_state=DECAYING -- the real recent-vs-lifetime trend is weakening; "
                          "engineering effort on a fading pattern is not yet justified.")
    elif validation.validation_state == taxonomy.VALIDATION_STATE_EMERGING:
        decision = taxonomy.DECISION_CONTINUE_OBSERVING
        reasoning.append("validation_state=EMERGING -- real evidence is accumulating but has not yet reached "
                          "Series 105's own VALIDATED bar.")
    elif not opportunity_quality_ok:
        decision = taxonomy.DECISION_CONTINUE_OBSERVING
        reasoning.append("validation_state=VALIDATED, but no real, positive Opportunity Assessment "
                          "(OPPORTUNITY_IDENTIFIED/GOOD_TRADE_TAKEN) was referenced -- a validated market pattern "
                          "alone, without real evidence of a genuine actionable opportunity, is not yet sufficient.")
    else:
        decision = taxonomy.DECISION_READY_FOR_ENGINEERING_REVIEW
        reasoning.append("validation_state=VALIDATED, no real contradicting observations, and at least one real "
                          "positive Opportunity Assessment is referenced -- sufficient evidence exists to justify "
                          "DESIGNING a controlled engineering proposal. This is NOT an instruction to implement "
                          "anything.")

    rid = _report_id(validation.hypothesis_label, validation.validation_id, decision, schema_version)

    stats = (
        ("occurrence_count", str(validation.occurrence_count)),
        ("diversity_count", str(validation.diversity_count)),
        ("consistency_ratio", f"{validation.consistency_ratio:.4f}"),
        ("replay_support_ratio", f"{validation.replay_support_ratio:.4f}"),
        ("causal_validity_ratio", f"{validation.causal_validity_ratio:.4f}"),
        ("positive_opportunity_count", str(len(positive_opportunities))),
        ("total_opportunity_references", str(len(opportunity_assessments))),
    )

    explanation = EngineeringEvidenceExplanation(
        report_id=rid, why_this_decision=tuple(reasoning), criteria_evaluated=tuple(criteria), schema_version=schema_version,
    )

    return EngineeringEvidenceReport(
        report_id=rid, generated_timestamp=generated_timestamp, hypothesis_label=validation.hypothesis_label,
        referenced_knowledge_validation_reports=(validation.validation_id,),
        referenced_evidence_packets=tuple(evidence_packet_ids),
        referenced_opportunity_assessments=tuple(o.assessment_id for o in opportunity_assessments),
        referenced_counterfactual_sessions=tuple(counterfactual_session_ids),
        referenced_phenomena_reports=tuple(phenomena_report_ids),
        referenced_decision_records=tuple(decision_record_ids),
        supporting_statistics=stats, supporting_reasoning=tuple(reasoning),
        contradictory_observations=tuple(contradictory_observations), known_limitations=limitations,
        decision=decision, explanation=explanation, provenance=_config.DEFAULT_PROVENANCE, schema_version=schema_version,
    )


def archive(report: EngineeringEvidenceReport, reason: Sequence[str], *, timestamp: str) -> EngineeringEvidenceReport:
    """The one, explicit, human-initiated override: a new, immutable
    report (never mutates the original, same correction-creates-a-new-
    record discipline as Series 101's EvidencePacket) with
    decision=ARCHIVED and the real, disclosed human reasoning. This is
    the ONLY way an EngineeringEvidenceReport can ever carry ARCHIVED --
    review_evidence() never assigns it."""
    if not reason:
        raise ValueError("archive() requires at least one real, disclosed reason -- an unexplained archive is never allowed")
    rid = _report_id(report.hypothesis_label, report.report_id, taxonomy.DECISION_ARCHIVED, report.schema_version)
    explanation = EngineeringEvidenceExplanation(
        report_id=rid, why_this_decision=tuple(reason),
        criteria_evaluated=(f"archived by explicit human action, real prior report {report.report_id}",),
        schema_version=report.schema_version,
    )
    return EngineeringEvidenceReport(
        report_id=rid, generated_timestamp=timestamp, hypothesis_label=report.hypothesis_label,
        referenced_knowledge_validation_reports=report.referenced_knowledge_validation_reports,
        referenced_evidence_packets=report.referenced_evidence_packets,
        referenced_opportunity_assessments=report.referenced_opportunity_assessments,
        referenced_counterfactual_sessions=report.referenced_counterfactual_sessions,
        referenced_phenomena_reports=report.referenced_phenomena_reports,
        referenced_decision_records=report.referenced_decision_records,
        supporting_statistics=report.supporting_statistics, supporting_reasoning=tuple(reason),
        contradictory_observations=report.contradictory_observations, known_limitations=report.known_limitations,
        decision=taxonomy.DECISION_ARCHIVED, explanation=explanation,
        provenance=_config.DEFAULT_PROVENANCE, schema_version=report.schema_version,
    )


def supersede(report: EngineeringEvidenceReport, superseded_by_hypothesis_label: str,
              reason: Sequence[str], *, timestamp: str) -> EngineeringEvidenceReport:
    """The other explicit, human-initiated override: a new, immutable
    report with decision=SUPERSEDED, citing the real, named replacement
    hypothesis that now subsumes this one (e.g. a broader pattern was
    refined into a more specific real finding). Never mutates the prior
    report -- same correction-creates-a-new-record discipline as
    archive(). This is the ONLY way an EngineeringEvidenceReport can ever
    carry SUPERSEDED -- review_evidence() never assigns it."""
    if not reason:
        raise ValueError("supersede() requires at least one real, disclosed reason -- an unexplained supersession is never allowed")
    if not superseded_by_hypothesis_label:
        raise ValueError("supersede() requires a real, named replacement hypothesis label")
    rid = _report_id(report.hypothesis_label, report.report_id, taxonomy.DECISION_SUPERSEDED, report.schema_version)
    full_reason = tuple(reason) + (f"superseded by real hypothesis: {superseded_by_hypothesis_label}",)
    explanation = EngineeringEvidenceExplanation(
        report_id=rid, why_this_decision=full_reason,
        criteria_evaluated=(f"superseded by explicit human action, real prior report {report.report_id}, "
                             f"replacement hypothesis {superseded_by_hypothesis_label}",),
        schema_version=report.schema_version,
    )
    return EngineeringEvidenceReport(
        report_id=rid, generated_timestamp=timestamp, hypothesis_label=report.hypothesis_label,
        referenced_knowledge_validation_reports=report.referenced_knowledge_validation_reports,
        referenced_evidence_packets=report.referenced_evidence_packets,
        referenced_opportunity_assessments=report.referenced_opportunity_assessments,
        referenced_counterfactual_sessions=report.referenced_counterfactual_sessions,
        referenced_phenomena_reports=report.referenced_phenomena_reports,
        referenced_decision_records=report.referenced_decision_records,
        supporting_statistics=report.supporting_statistics, supporting_reasoning=full_reason,
        contradictory_observations=report.contradictory_observations, known_limitations=report.known_limitations,
        decision=taxonomy.DECISION_SUPERSEDED, explanation=explanation,
        provenance=_config.DEFAULT_PROVENANCE, schema_version=report.schema_version,
    )
