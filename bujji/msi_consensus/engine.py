"""Multi-Domain Consensus Intelligence engine — pure functions, no
side effects, no wall-clock reads inside any computation.

---------------------------------------------------------------------
Design note — domain-count/domain-type agnosticism (this package's
central design mandate, Deliverable 3's guidance point 3: "additional
brains can be added without changing the consensus algorithm -- only
by registering new assessment types"):
---------------------------------------------------------------------
This engine never imports, references, or branches on a SPECIFIC
brain's real model type (no `PriceStructureAssessment`,
`MarketStructureAssessment`, or any future `VolatilityStructure...`
type appears anywhere in this file). It consumes exactly one generic
shape, `DomainAssessmentView`, defined below. A caller (a test, a
future runtime wiring layer, a demonstration) is responsible for
reducing any real brain's real assessment into a `DomainAssessmentView`
-- a thin, external, per-brain translation layer, exactly mirroring how
Series 79's own Deliverable-10-equivalent demonstration translated
`PriceStructureAssessment`/`MarketStructureAssessment` into Series 77's
`DomainSignal`. Adding a new brain (e.g. a real, future Series 80
Volatility Structure brain) therefore requires writing ONE new
translation function outside this package and registering its domain
name in a caller-supplied expected-domain registry -- zero changes to
any function in this file.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Dict, Optional, Sequence, Tuple

from . import config as _config
from . import taxonomy
from .models import Contradiction, ConsensusAssessment, Explanation


# ---------------------------------------------------------------------------
# DomainAssessmentView — MDCI's OWN minimal internal input shape.
# Deliberately NOT `bujji.msi_price_structure.models.PriceStructureAssessment`
# or `bujji.msi_market_structure.models.MarketStructureAssessment` --
# see module docstring and `__init__.py`'s isolation mandate.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class DomainAssessmentView:
    domain_name: str                       # Should be one of the caller's expected-domain registry (e.g. taxonomy.ALL_MSI_DOMAINS), but MDCI does not require it to be -- an unrecognized name simply cannot appear in missing_domains accounting and is otherwise treated identically.
    lean: str                              # One of taxonomy.ALL_LEANS -- computed by the CALLER's own translation layer, never by this package.
    confidence: float                      # The originating brain's own self-reported confidence, 0.0-1.0.
    evidence_ids: Tuple[str, ...] = ()     # References only -- never copies -- the originating brain's evidence/observation ids.
    source_assessment_id: Optional[str] = None   # The originating brain's own assessment_id, referenced by plain string only (never an imported type) -- None for a view with no single backing assessment (e.g. a purely synthetic/mock view).


def _considered_views(domain_views: Sequence[DomainAssessmentView]) -> Tuple[DomainAssessmentView, ...]:
    return tuple(v for v in domain_views if v.lean in taxonomy.CONSIDERED_LEANS)


def _winning_lean(considered: Sequence[DomainAssessmentView]) -> Optional[str]:
    if not considered:
        return None
    counts: Dict[str, int] = {}
    for v in considered:
        counts[v.lean] = counts.get(v.lean, 0) + 1
    best_count = max(counts.values())
    winners = sorted(lean for lean, c in counts.items() if c == best_count)
    if len(winners) == 1:
        return winners[0]
    # Tie (including an all-different 1-1-1 split): NEUTRAL_LEANING is
    # the conservative default, mirroring Series 77's "ties resolve to
    # the conservative NEUTRAL_FORMING read" precedent (never overclaim
    # a directional winner from a genuine tie).
    return taxonomy.LEAN_NEUTRAL


def compute_agreement(
    domain_views: Sequence[DomainAssessmentView],
) -> Tuple[Tuple[str, ...], Tuple[str, ...]]:
    """Returns (agreeing_domains, conflicting_domains), both sorted by
    domain_name for determinism. AMBIGUOUS_LEANING views vote in
    neither list -- they are excluded from the vote entirely (see
    taxonomy.py's module docstring)."""
    considered = _considered_views(domain_views)
    winning_lean = _winning_lean(considered)
    if winning_lean is None:
        return (), ()
    agreeing = tuple(sorted(v.domain_name for v in considered if v.lean == winning_lean))
    conflicting = tuple(sorted(v.domain_name for v in considered if v.lean != winning_lean))
    return agreeing, conflicting


def compute_consensus_level(agreement_ratio: Optional[float]) -> str:
    """Deterministic function of `agreement_ratio` alone (see
    config.py's disclosed thresholds). `None` (zero considered domains)
    and `0.0` (zero agreement among considered domains) both map to
    NO_CONSENSUS -- there is nothing, or nothing but conflict, to call
    consensus on."""
    if agreement_ratio is None or agreement_ratio <= 0.0:
        return taxonomy.CONSENSUS_NO_CONSENSUS
    if agreement_ratio >= _config.CONSENSUS_UNANIMOUS_MIN_RATIO:
        return taxonomy.CONSENSUS_UNANIMOUS
    if agreement_ratio >= _config.CONSENSUS_STRONG_MIN_RATIO:
        return taxonomy.CONSENSUS_STRONG
    if agreement_ratio >= _config.CONSENSUS_MODERATE_MIN_RATIO:
        return taxonomy.CONSENSUS_MODERATE
    return taxonomy.CONSENSUS_WEAK


def compute_evidence_sufficiency(
    domain_views: Sequence[DomainAssessmentView],
    expected_domains: Sequence[str] = _config.DEFAULT_EXPECTED_DOMAINS,
) -> str:
    """Factors in coverage (participating vs. expected domain count)
    AND evidence density per participating domain -- see config.py's
    disclosed formula. Deliberately independent of consensus_level
    (agreement/conflict) -- reads only evidence_ids/participation
    counts, never lean/agreement values."""
    if not domain_views or not expected_domains:
        return taxonomy.SUFFICIENCY_INSUFFICIENT
    coverage_ratio = len(domain_views) / len(expected_domains)
    density_terms = [
        min(1.0, len(v.evidence_ids) / _config.EVIDENCE_DENSITY_TARGET) for v in domain_views
    ]
    evidence_density = sum(density_terms) / len(density_terms)
    score = coverage_ratio * evidence_density
    if score >= _config.SUFFICIENCY_ROBUST_MIN_SCORE:
        return taxonomy.SUFFICIENCY_ROBUST
    if score >= _config.SUFFICIENCY_ADEQUATE_MIN_SCORE:
        return taxonomy.SUFFICIENCY_ADEQUATE
    if score >= _config.SUFFICIENCY_LIMITED_MIN_SCORE:
        return taxonomy.SUFFICIENCY_LIMITED
    return taxonomy.SUFFICIENCY_INSUFFICIENT


def compute_missing_domains(
    domain_views: Sequence[DomainAssessmentView],
    expected_domains: Sequence[str] = _config.DEFAULT_EXPECTED_DOMAINS,
) -> Tuple[str, ...]:
    participating = {v.domain_name for v in domain_views}
    return tuple(sorted(d for d in expected_domains if d not in participating))


def _per_domain_calibration(view: DomainAssessmentView) -> Optional[str]:
    """None means "not enough basis to judge this domain" (zero cited
    evidence) -- excluded from the aggregate vote, never defaulted to
    WELL_CALIBRATED."""
    n_evidence = len(view.evidence_ids)
    if n_evidence == 0:
        return None
    claims_high = view.confidence >= _config.CALIBRATION_HIGH_CONFIDENCE_MIN
    claims_low = view.confidence <= _config.CALIBRATION_LOW_CONFIDENCE_MAX
    thin = n_evidence <= _config.CALIBRATION_THIN_EVIDENCE_MAX_COUNT
    dense = n_evidence >= _config.CALIBRATION_DENSE_EVIDENCE_MIN_COUNT
    if claims_high and thin:
        return taxonomy.CALIBRATION_OVERCONFIDENT
    if claims_low and dense:
        return taxonomy.CALIBRATION_UNDERCONFIDENT
    return taxonomy.CALIBRATION_WELL_CALIBRATED


def compute_confidence_calibration(domain_views: Sequence[DomainAssessmentView]) -> str:
    """Majority vote across every JUDGED domain view (see
    `_per_domain_calibration`). A tie, or zero judged domains, resolves
    to UNKNOWN -- the conservative default when there isn't enough
    basis to assert a specific calibration verdict."""
    judged = [c for c in (_per_domain_calibration(v) for v in domain_views) if c is not None]
    if not judged:
        return taxonomy.CALIBRATION_UNKNOWN
    counts: Dict[str, int] = {}
    for c in judged:
        counts[c] = counts.get(c, 0) + 1
    best_count = max(counts.values())
    winners = sorted(label for label, c in counts.items() if c == best_count)
    if len(winners) == 1:
        return winners[0]
    return taxonomy.CALIBRATION_UNKNOWN


def compute_contradiction_density(considered_count: int, conflicting_count: int) -> float:
    """conflicting_domain_count / considered_domain_count, or 0.0 when
    there are zero considered domains (see config.py's disclosed
    formula)."""
    if considered_count == 0:
        return 0.0
    return conflicting_count / considered_count


def detect_cross_domain_contradictions(
    domain_views: Sequence[DomainAssessmentView],
) -> Tuple[Contradiction, ...]:
    """Surfaces every pairwise disagreement among CONSIDERED domain
    views (differing leans). NEVER resolves a contradiction -- there is
    no averaging, no discarding, no "drop the minority" branch anywhere
    in this function. Pairs are emitted in a deterministic
    (domain_name-sorted) order."""
    considered = sorted(_considered_views(domain_views), key=lambda v: v.domain_name)
    contradictions = []
    for i in range(len(considered)):
        for j in range(i + 1, len(considered)):
            a, b = considered[i], considered[j]
            if a.lean != b.lean:
                contradictions.append(
                    Contradiction(
                        dimension_a=a.domain_name,
                        value_a=a.lean,
                        dimension_b=b.domain_name,
                        value_b=b.lean,
                        reason=(
                            f"{a.domain_name} leans {a.lean} while {b.domain_name} leans "
                            f"{b.lean} -- directly conflicting reads within the same "
                            f"consensus cycle; never resolved, only surfaced."
                        ),
                    )
                )
    return tuple(contradictions)


def _assessment_id(
    domain_views: Sequence[DomainAssessmentView],
    consensus_level: str,
    evidence_sufficiency: str,
    confidence_calibration: str,
    schema_version: str,
) -> str:
    sorted_views = sorted(domain_views, key=lambda v: v.domain_name)
    payload = repr(
        (
            tuple(
                (v.domain_name, v.lean, round(v.confidence, 6), tuple(v.evidence_ids), v.source_assessment_id)
                for v in sorted_views
            ),
            consensus_level,
            evidence_sufficiency,
            confidence_calibration,
            schema_version,
        )
    )
    return hashlib.md5(payload.encode("utf-8")).hexdigest()


def build_explanation(
    domain_views: Sequence[DomainAssessmentView],
    assessment: ConsensusAssessment,
    previous_assessment: Optional[ConsensusAssessment],
    *,
    schema_version: str = _config.SCHEMA_VERSION,
) -> Explanation:
    considered = _considered_views(domain_views)

    # 3. which evidence is missing -- missing_domains PLUS any
    # participating domain that cited zero evidence_ids at all.
    zero_evidence_domains = tuple(sorted(v.domain_name for v in domain_views if not v.evidence_ids))
    which_evidence_is_missing = tuple(sorted(set(assessment.missing_domains) | set(zero_evidence_domains)))

    # 4. why consensus is high/low -- a real computed statement.
    n_considered = len(considered)
    n_agreeing = len(assessment.agreeing_domains)
    n_conflicting = len(assessment.conflicting_domains)
    ratio = (n_agreeing / n_considered) if n_considered else None
    ratio_text = f"{ratio:.2f}" if ratio is not None else "undefined (no considered domains)"
    why_consensus_is_high_or_low = (
        f"consensus_level={assessment.consensus_level}: {n_agreeing} of {n_considered} considered "
        f"domain(s) agreed on the winning lean (agreement_ratio={ratio_text}); "
        f"{n_conflicting} conflicted; {len(assessment.missing_domains)} expected domain(s) "
        f"did not participate at all."
    )

    # 5. what additional domains would increase confidence -- purely
    # mechanical, never speculative.
    would_increase = []
    if assessment.missing_domains:
        would_increase.append(
            f"participation from missing domain(s): {', '.join(assessment.missing_domains)}"
        )
    if assessment.conflicting_domains:
        would_increase.append(
            f"resolution of conflicting domain(s): {', '.join(assessment.conflicting_domains)}"
        )
    if zero_evidence_domains:
        would_increase.append(
            f"additional cited evidence from domain(s) reporting none: {', '.join(zero_evidence_domains)}"
        )
    what_additional_domains_would_increase_confidence = tuple(would_increase)

    what_changed: Optional[str] = None
    if previous_assessment is not None:
        diffs = []
        for field in ("consensus_level", "evidence_sufficiency", "confidence_calibration"):
            old = getattr(previous_assessment, field)
            new = getattr(assessment, field)
            if old != new:
                diffs.append(f"{field}: {old} -> {new}")
        if diffs:
            what_changed = (
                f"vs. previous assessment {previous_assessment.assessment_id}: " + "; ".join(diffs)
            )
        else:
            what_changed = f"no change vs. previous assessment {previous_assessment.assessment_id}"

    return Explanation(
        assessment_id=assessment.assessment_id,
        which_domains_agree=assessment.agreeing_domains,
        which_domains_disagree=assessment.conflicting_domains,
        which_evidence_is_missing=which_evidence_is_missing,
        why_consensus_is_high_or_low=why_consensus_is_high_or_low,
        what_additional_domains_would_increase_confidence=what_additional_domains_would_increase_confidence,
        what_changed=what_changed,
        schema_version=schema_version,
    )


def compute_consensus(
    domain_views: Sequence[DomainAssessmentView],
    *,
    timestamp: str,
    expected_domains: Sequence[str] = _config.DEFAULT_EXPECTED_DOMAINS,
    schema_version: str = _config.SCHEMA_VERSION,
    provenance: str = _config.DEFAULT_PROVENANCE,
) -> ConsensusAssessment:
    """The one real entrypoint that builds a `ConsensusAssessment` from
    a generic sequence of `DomainAssessmentView`s. Never imports, never
    branches on, a specific brain's real model type (see module
    docstring)."""
    considered = _considered_views(domain_views)
    agreeing, conflicting = compute_agreement(domain_views)
    agreement_ratio = (len(agreeing) / len(considered)) if considered else None
    consensus_level = compute_consensus_level(agreement_ratio)
    evidence_sufficiency = compute_evidence_sufficiency(domain_views, expected_domains)
    confidence_calibration = compute_confidence_calibration(domain_views)
    missing_domains = compute_missing_domains(domain_views, expected_domains)
    contradiction_density = compute_contradiction_density(len(considered), len(conflicting))

    participating_domains = tuple(sorted(v.domain_name for v in domain_views))
    supporting_assessment_ids = tuple(
        sorted({v.source_assessment_id for v in domain_views if v.source_assessment_id is not None})
    )

    assessment_id = _assessment_id(
        domain_views, consensus_level, evidence_sufficiency, confidence_calibration, schema_version
    )

    return ConsensusAssessment(
        assessment_id=assessment_id,
        timestamp=timestamp,
        participating_domains=participating_domains,
        agreeing_domains=agreeing,
        conflicting_domains=conflicting,
        missing_domains=missing_domains,
        consensus_level=consensus_level,
        evidence_sufficiency=evidence_sufficiency,
        contradiction_density=contradiction_density,
        confidence_calibration=confidence_calibration,
        supporting_assessment_ids=supporting_assessment_ids,
        explanation=Explanation(
            assessment_id=assessment_id, which_domains_agree=(), which_domains_disagree=(),
            which_evidence_is_missing=(), why_consensus_is_high_or_low="", what_changed=None,
            what_additional_domains_would_increase_confidence=(), schema_version=schema_version,
        ),
        provenance=provenance,
        schema_version=schema_version,
    )


def compute_consensus_with_explanation(
    domain_views: Sequence[DomainAssessmentView],
    previous_assessment: Optional[ConsensusAssessment],
    *,
    timestamp: str,
    expected_domains: Sequence[str] = _config.DEFAULT_EXPECTED_DOMAINS,
    schema_version: str = _config.SCHEMA_VERSION,
    provenance: str = _config.DEFAULT_PROVENANCE,
) -> ConsensusAssessment:
    """Convenience wrapper: `compute_consensus` followed immediately by
    `build_explanation`, threading `previous_assessment` through so
    `Explanation.what_changed` is populated. This is the entrypoint
    `runner.py` uses for every produced assessment."""
    assessment = compute_consensus(
        domain_views, timestamp=timestamp, expected_domains=expected_domains,
        schema_version=schema_version, provenance=provenance,
    )
    explanation = build_explanation(domain_views, assessment, previous_assessment, schema_version=schema_version)
    # Rebuild with the real explanation (ConsensusAssessment is frozen).
    return ConsensusAssessment(
        assessment_id=assessment.assessment_id,
        timestamp=assessment.timestamp,
        participating_domains=assessment.participating_domains,
        agreeing_domains=assessment.agreeing_domains,
        conflicting_domains=assessment.conflicting_domains,
        missing_domains=assessment.missing_domains,
        consensus_level=assessment.consensus_level,
        evidence_sufficiency=assessment.evidence_sufficiency,
        contradiction_density=assessment.contradiction_density,
        confidence_calibration=assessment.confidence_calibration,
        supporting_assessment_ids=assessment.supporting_assessment_ids,
        explanation=explanation,
        provenance=assessment.provenance,
        schema_version=assessment.schema_version,
    )
