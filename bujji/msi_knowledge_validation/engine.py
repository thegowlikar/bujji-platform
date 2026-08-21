"""KVE engine — Series 105. Pure functions: no IO, no state, no
wall-clock reads, no randomness. ZERO imports from any other bujji
package -- same isolation discipline as Series 104's OAE, and for the
same reason: KVE only measures caller-supplied `HypothesisOccurrence`
records, it never re-derives or re-invokes anything Series 100-104
already computed.

First principle, structurally enforced: KVE does not invent hypotheses
-- there is no function anywhere in this module that generates a
HypothesisOccurrence; every occurrence measured here is real and
caller-supplied. KVE only measures and classifies what it's given."""
from __future__ import annotations

import hashlib
from datetime import datetime
from typing import List, Sequence, Tuple

from . import config as _config
from . import taxonomy
from .models import HypothesisOccurrence, KnowledgeValidationExplanation, KnowledgeValidationReport


def validate_causal_order(occurrences: Sequence[HypothesisOccurrence]) -> Tuple[bool, Tuple[str, ...]]:
    """Real occurrences must be supplied in non-decreasing chronological
    order -- historical_occurrence_statistics and the growth/decay trend
    below both depend on a real, honest timeline, never a reordered one."""
    violations = []
    for prev, cur in zip(occurrences, occurrences[1:]):
        if cur.timestamp < prev.timestamp:
            violations.append(f"{cur.day}@{cur.timestamp} appears after {prev.day}@{prev.timestamp} but sorts earlier")
    if violations:
        return False, tuple(violations)
    return True, (f"{len(occurrences)} real occurrence(s) in non-decreasing chronological order",)


def _trend(recent: Sequence[HypothesisOccurrence], all_occ: Sequence[HypothesisOccurrence]) -> str:
    """Real, disclosed, non-tunable comparison of recent vs. lifetime
    occurrence rate -- same declarative approach as Series 100's own
    decay assessment, reimplemented locally here (KVE cannot import MLE,
    per its own zero-sibling-import isolation)."""
    if len(all_occ) < 2 or len(recent) < 2:
        return taxonomy.TREND_UNKNOWN

    def _rate(occs: Sequence[HypothesisOccurrence]) -> float:
        try:
            t0 = datetime.fromisoformat(occs[0].timestamp)
            t1 = datetime.fromisoformat(occs[-1].timestamp)
            span_days = max(1.0, (t1 - t0).total_seconds() / 86400.0)
        except ValueError:
            return 0.0
        return len(occs) / span_days

    lifetime_rate = _rate(all_occ)
    recent_rate = _rate(recent)
    if lifetime_rate <= 0:
        return taxonomy.TREND_UNKNOWN
    ratio = recent_rate / lifetime_rate
    if ratio >= _config.TREND_GROWING_RATIO:
        return taxonomy.TREND_GROWING
    if ratio <= _config.TREND_WEAKENING_RATIO:
        return taxonomy.TREND_WEAKENING
    return taxonomy.TREND_STABLE


def _validation_id(hypothesis_label: str, occurrence_count: int, schema_version: str) -> str:
    content = "|".join([hypothesis_label, str(occurrence_count), schema_version])
    return "KV-" + hashlib.md5(content.encode("utf-8")).hexdigest()[:24]


def validate_hypothesis(
    hypothesis_label: str, occurrences: Sequence[HypothesisOccurrence], *, generated_timestamp: str,
    schema_version: str = _config.SCHEMA_VERSION,
) -> KnowledgeValidationReport:
    """Produces exactly one real KnowledgeValidationReport for one real
    hypothesis. Raises ValueError if the real occurrence sequence is not
    causally ordered -- validation never proceeds against an invalid
    timeline."""
    ok, order_reasons = validate_causal_order(occurrences)
    if not ok:
        raise ValueError(f"occurrences are not causally ordered: {order_reasons}")

    n = len(occurrences)
    metrics: List[str] = []

    if n == 0:
        state = taxonomy.STATE_NOT_OBSERVED
        consistency = taxonomy.CONSISTENCY_UNKNOWN
        consistency_ratio = 0.0
        replay_ratio = 0.0
        causal_ratio = 0.0
        diversity = 0
        growth = taxonomy.TREND_UNKNOWN
        decay = taxonomy.TREND_UNKNOWN
        why = ["zero real occurrences supplied -- NOT_OBSERVED is the only honest state."]
    else:
        valid_and_legal = sum(1 for o in occurrences if o.causally_valid and o.counterfactual_legal)
        consistency_ratio = valid_and_legal / n
        causal_ratio = sum(1 for o in occurrences if o.causally_valid) / n
        replay_ratio = sum(1 for o in occurrences if o.replay_reproducible) / n
        diversity = len({o.market_classification for o in occurrences})
        metrics.append(f"occurrence_count={n}, consistency_ratio={consistency_ratio:.2f}, "
                        f"causal_validity_ratio={causal_ratio:.2f}, replay_support_ratio={replay_ratio:.2f}, "
                        f"diversity_count={diversity}")

        half = max(1, n // 2)
        recent = occurrences[-half:]
        growth = _trend(recent, occurrences)
        decay = growth  # a single real rate comparison serves as both signals -- WEAKENING is real decay, GROWING is real growth, STABLE is neither.
        metrics.append(f"real recency trend (recent {len(recent)} vs all {n}): {growth}")

        consistency = (
            taxonomy.CONSISTENCY_CONSISTENT if consistency_ratio >= 0.5
            else taxonomy.CONSISTENCY_INCONSISTENT
        )

        if consistency_ratio < taxonomy.INVALIDATED_MAX_CONSISTENCY_RATIO and n >= taxonomy.REPEATED_MIN_OCCURRENCES:
            state = taxonomy.STATE_INVALIDATED
            why = [f"consistency_ratio={consistency_ratio:.2f} is below the real "
                   f"{taxonomy.INVALIDATED_MAX_CONSISTENCY_RATIO} threshold across {n} real occurrences -- "
                   f"the majority of real evidence does not support this pattern."]
        elif n == 1:
            state = taxonomy.STATE_OBSERVED
            why = ["exactly 1 real occurrence -- a single observation is never evidence on its own."]
        elif n < taxonomy.EMERGING_MIN_OCCURRENCES:
            state = taxonomy.STATE_REPEATED
            why = [f"{n} real occurrences, below the real {taxonomy.EMERGING_MIN_OCCURRENCES}-occurrence "
                   f"EMERGING threshold."]
        elif decay == taxonomy.TREND_WEAKENING:
            state = taxonomy.STATE_DECAYING
            why = [f"{n} real occurrences, but the real recent-vs-lifetime rate comparison shows WEAKENING -- "
                   f"this pattern is fading, disclosed rather than silently kept at its prior state."]
        elif (n >= taxonomy.VALIDATED_MIN_OCCURRENCES and diversity >= taxonomy.VALIDATED_MIN_DIVERSITY
              and replay_ratio >= taxonomy.VALIDATED_MIN_REPLAY_SUPPORT_RATIO
              and consistency == taxonomy.CONSISTENCY_CONSISTENT):
            state = taxonomy.STATE_VALIDATED
            why = [f"{n} real occurrences (>= {taxonomy.VALIDATED_MIN_OCCURRENCES}), diversity_count={diversity} "
                   f"(>= {taxonomy.VALIDATED_MIN_DIVERSITY}), replay_support_ratio={replay_ratio:.2f} "
                   f"(>= {taxonomy.VALIDATED_MIN_REPLAY_SUPPORT_RATIO}), consistency=CONSISTENT -- "
                   f"all real VALIDATED criteria met."]
        else:
            state = taxonomy.STATE_EMERGING
            why = [f"{n} real occurrences (>= {taxonomy.EMERGING_MIN_OCCURRENCES}) and consistency={consistency}, "
                   f"but not yet meeting all real VALIDATED criteria (needs {taxonomy.VALIDATED_MIN_OCCURRENCES}+ "
                   f"occurrences, {taxonomy.VALIDATED_MIN_DIVERSITY}+ diversity, "
                   f"{taxonomy.VALIDATED_MIN_REPLAY_SUPPORT_RATIO}+ replay support ratio)."]

    evidence_packets = tuple(sorted({o.evidence_packet_id for o in occurrences if o.evidence_packet_id}))
    opportunity_assessments = tuple(sorted({o.opportunity_assessment_id for o in occurrences if o.opportunity_assessment_id}))
    counterfactual_sessions = tuple(sorted({o.counterfactual_session_id for o in occurrences if o.counterfactual_session_id}))
    phenomena = tuple(sorted({o.phenomena_report_id for o in occurrences if o.phenomena_report_id}))

    stats: List[Tuple[str, int]] = []
    for i, o in enumerate(occurrences, start=1):
        stats.append((o.day, i))

    vid = _validation_id(hypothesis_label, n, schema_version)
    explanation = KnowledgeValidationExplanation(
        validation_id=vid, why_this_state=tuple(why), metrics_considered=tuple(metrics), schema_version=schema_version,
    )

    return KnowledgeValidationReport(
        validation_id=vid, hypothesis_label=hypothesis_label, generated_timestamp=generated_timestamp,
        occurrence_count=n, diversity_count=diversity, consistency=consistency, consistency_ratio=consistency_ratio,
        replay_support_ratio=replay_ratio, causal_validity_ratio=causal_ratio,
        evidence_growth=growth, evidence_decay=decay, validation_state=state,
        supporting_evidence_packets=evidence_packets, supporting_opportunity_assessments=opportunity_assessments,
        supporting_counterfactual_sessions=counterfactual_sessions, supporting_phenomena=phenomena,
        historical_occurrence_statistics=tuple(stats), explanation=explanation,
        provenance=_config.DEFAULT_PROVENANCE, schema_version=schema_version,
    )
