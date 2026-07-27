"""Price Structure Intelligence engine — pure functions, no state, no IO.

Implements Deliverable 1's first-principles concepts (Trend, Swing,
Compression, Expansion, Balance) as small, independently-testable pure
functions, one concern per function, mirroring how MIC v2's own
engine.py files (and Series 75/76/77's engines) are structured.

Consumes `bujji.market_episode.models.Episode` objects (Series 76) plus
the `bujji.live_market_events.models.MarketEvent` objects they
reference by id. An `Episode` never carries a MarketEvent's payload
(by Series 76 design), so this engine is always handed the actual
MarketEvent objects (looked up by id) alongside the episodes — this is
necessary plumbing, not a deviation from any prior series' provenance
discipline: the output still references events/observations by id only,
never copies their payload.

Nothing here predicts market direction, selects a trade structure or a
price level for entry, or scores trade-outcome likelihood — purely
descriptive interpretation into the Deliverable 1 vocabulary.
"""
from __future__ import annotations

import hashlib
from typing import Dict, List, Optional, Sequence, Tuple

from bujji.live_market_events.models import MarketEvent
from bujji.market_episode.models import Episode

from . import config as _config
from . import taxonomy
from .models import Contradiction, Explanation, PriceStructureAssessment


# ---------------------------------------------------------------------------
# Evidence extraction — collect the ordered price-structure-relevant
# MarketEvents referenced (transitively) by a tuple of Episodes.
# ---------------------------------------------------------------------------
def _price_events_in_order(
    episodes: Tuple[Episode, ...],
    events_by_id: Dict[str, MarketEvent],
) -> Tuple[MarketEvent, ...]:
    seen: List[MarketEvent] = []
    seen_ids = set()
    for episode in episodes:
        for event_id in episode.originating_event_ids:
            if event_id in seen_ids:
                continue
            event = events_by_id.get(event_id)
            if event is None or event.event_type not in taxonomy.PRICE_STRUCTURE_EVENT_TYPES:
                continue
            seen.append(event)
            seen_ids.add(event_id)
    seen.sort(key=lambda e: (e.timestamp, e.event_id))
    return tuple(seen)


def _deltas(price_events: Tuple[MarketEvent, ...]) -> Tuple[float, ...]:
    out: List[float] = []
    for event in price_events:
        delta = event.detail.get("delta")
        if isinstance(delta, (int, float)):
            out.append(float(delta))
    return tuple(out)


def _sign(x: float) -> int:
    if x > 0:
        return 1
    if x < 0:
        return -1
    return 0


def _trailing_run_length(signs: Sequence[int]) -> int:
    """Length of the trailing run of equal, non-zero signs."""
    if not signs:
        return 0
    last = signs[-1]
    if last == 0:
        return 0
    run = 0
    for s in reversed(signs):
        if s == last:
            run += 1
        else:
            break
    return run


def _trailing_magnitude_run(deltas: Sequence[float], *, decreasing: bool) -> int:
    """Length of the trailing run of strictly monotonic |delta| values
    (decreasing -> compression proxy; increasing -> expansion proxy)."""
    mags = [abs(d) for d in deltas]
    if len(mags) < 2:
        return 0
    run = 1
    for i in range(len(mags) - 1, 0, -1):
        if decreasing and mags[i] < mags[i - 1]:
            run += 1
        elif not decreasing and mags[i] > mags[i - 1]:
            run += 1
        else:
            break
    return run


# ---------------------------------------------------------------------------
# Deliverable 1 — Swing: "a local extreme confirmed by subsequent
# opposing structure." Confirmed the moment a reversal (sign change) is
# observed in the price-delta sequence; FORMING if the run is still
# directionally consistent with no reversal yet; NO_SWING_DATA if fewer
# than 2 price events exist at all.
# ---------------------------------------------------------------------------
def derive_swing_state(price_events: Tuple[MarketEvent, ...]) -> str:
    deltas = _deltas(price_events)
    if len(deltas) < 2:
        return taxonomy.SWING_NO_DATA
    signs = [_sign(d) for d in deltas]
    if signs[-1] != 0 and signs[-2] != 0 and signs[-1] != signs[-2]:
        return taxonomy.SWING_CONFIRMED  # subsequent OPPOSING structure just occurred.
    return taxonomy.SWING_FORMING


# ---------------------------------------------------------------------------
# Deliverable 1 — Trend: "a directional sequence of higher/lower swing
# structure the market is currently extending." Derived from the
# trailing run length of same-signed deltas.
# ---------------------------------------------------------------------------
def derive_trend_state(price_events: Tuple[MarketEvent, ...]) -> str:
    deltas = _deltas(price_events)
    if len(deltas) < 2:
        return taxonomy.TREND_NONE
    signs = [_sign(d) for d in deltas]
    run = _trailing_run_length(signs)
    if run >= _config.TREND_ESTABLISHED_MIN_RUN:
        return taxonomy.TREND_ESTABLISHED
    if run >= _config.TREND_EMERGING_MIN_RUN:
        return taxonomy.TREND_EMERGING
    # run == 1: the latest delta broke a prior run of the opposite
    # direction. If that prior (broken) run was itself at least
    # EMERGING-length, this reads as a trend that just lost momentum
    # (Deliverable 1's Momentum/Exhaustion concepts) rather than "no
    # trend" — WEAKENING_TREND, not NO_TREND.
    if run == 1 and len(signs) >= _config.TREND_EMERGING_MIN_RUN + 1:
        prior_signs = signs[:-1]
        prior_run = _trailing_run_length(prior_signs)
        if prior_run >= _config.TREND_EMERGING_MIN_RUN and prior_signs[-1] != signs[-1]:
            return taxonomy.TREND_WEAKENING
    return taxonomy.TREND_NONE


# ---------------------------------------------------------------------------
# Series 85 addendum — purely additive exposure of a value
# `derive_trend_state` above already computes internally (`signs[-1]`)
# but never returned. Zero change to `derive_trend_state` itself: this
# is a separate function, called with the same inputs, re-reading the
# same deterministic delta-sign sequence. None when trend_state is
# TREND_NONE (no run exists to have a sign); DIRECTION_UP/DOWN
# otherwise, from the sign of the trailing run identified by
# `derive_trend_state`'s own logic.
# ---------------------------------------------------------------------------
def derive_trend_direction_signal(price_events: Tuple[MarketEvent, ...], trend_state: str) -> Optional[str]:
    if trend_state == taxonomy.TREND_NONE:
        return None
    deltas = _deltas(price_events)
    if len(deltas) < 2:
        return None
    signs = [_sign(d) for d in deltas]
    trailing_sign = signs[-1]
    if trailing_sign > 0:
        return taxonomy.DIRECTION_UP
    if trailing_sign < 0:
        return taxonomy.DIRECTION_DOWN
    return None


# ---------------------------------------------------------------------------
# Deliverable 1 — Compression/Expansion: sibling "potential energy"
# conditions. NECESSARILY APPROXIMATE — no real range/volatility data
# exists yet (Volatility Structure brain, MSI Brain 3, is not built),
# so |delta| of consecutive price events is the only proxy available.
# ---------------------------------------------------------------------------
def derive_compression_state(price_events: Tuple[MarketEvent, ...]) -> str:
    deltas = _deltas(price_events)
    run = _trailing_magnitude_run(deltas, decreasing=True)
    if run >= _config.COMPRESSION_CONFIRMED_MIN_RUN:
        return taxonomy.COMPRESSION_CONFIRMED
    if run >= _config.COMPRESSION_EARLY_MIN_RUN:
        return taxonomy.COMPRESSION_EARLY
    return taxonomy.COMPRESSION_NOT_DETECTED


def derive_expansion_state(price_events: Tuple[MarketEvent, ...]) -> str:
    deltas = _deltas(price_events)
    run = _trailing_magnitude_run(deltas, decreasing=False)
    if run >= _config.EXPANSION_CONFIRMED_MIN_RUN:
        return taxonomy.EXPANSION_CONFIRMED
    if run >= _config.EXPANSION_EARLY_MIN_RUN:
        return taxonomy.EXPANSION_EARLY
    return taxonomy.EXPANSION_NOT_DETECTED


# ---------------------------------------------------------------------------
# Deliverable 1 — Balance/Imbalance: balance_ratio = |sum(deltas)| /
# sum(|deltas|). Near 0 -> price oscillated and net-cancelled (balance);
# near 1 -> every move was the same direction (imbalance).
# ---------------------------------------------------------------------------
def derive_balance_state(price_events: Tuple[MarketEvent, ...]) -> str:
    deltas = _deltas(price_events)
    if not deltas:
        return taxonomy.BALANCE_UNKNOWN
    total_abs = sum(abs(d) for d in deltas)
    if total_abs == 0:
        return taxonomy.BALANCE_IN_BALANCE
    ratio = abs(sum(deltas)) / total_abs
    if ratio < _config.BALANCE_RATIO_LOW_THRESHOLD:
        return taxonomy.BALANCE_IN_BALANCE
    if ratio >= _config.BALANCE_RATIO_HIGH_THRESHOLD:
        return taxonomy.BALANCE_IMBALANCED
    return taxonomy.BALANCE_TRANSITIONING


# ---------------------------------------------------------------------------
# Deliverable 2/4 — structure_state: the composite/overall read,
# deterministically derived from trend_state + balance_state (see
# taxonomy.py's module docstring for why compression/expansion are
# deliberately excluded from this composite).
# ---------------------------------------------------------------------------
def derive_structure_state(trend_state: str, balance_state: str) -> str:
    if trend_state == taxonomy.TREND_ESTABLISHED:
        return taxonomy.STRUCTURE_TRENDING
    if trend_state == taxonomy.TREND_WEAKENING:
        return taxonomy.STRUCTURE_CORRECTING
    if balance_state == taxonomy.BALANCE_IN_BALANCE:
        return taxonomy.STRUCTURE_BALANCE
    if trend_state == taxonomy.TREND_EMERGING:
        return taxonomy.STRUCTURE_TRANSITIONING
    if balance_state in (taxonomy.BALANCE_TRANSITIONING, taxonomy.BALANCE_IMBALANCED):
        return taxonomy.STRUCTURE_TRANSITIONING
    return taxonomy.STRUCTURE_UNKNOWN


# ---------------------------------------------------------------------------
# Deliverable 5 — contradiction detection. Surfaced, never hidden.
# ---------------------------------------------------------------------------
def detect_contradictions(
    *,
    trend_state: str,
    swing_state: str,
    balance_state: str,
    compression_state: str,
    expansion_state: str,
) -> Tuple[Contradiction, ...]:
    contradictions: List[Contradiction] = []

    if trend_state == taxonomy.TREND_ESTABLISHED and balance_state == taxonomy.BALANCE_IN_BALANCE:
        contradictions.append(Contradiction(
            dimension_a="trend_state", value_a=trend_state,
            dimension_b="balance_state", value_b=balance_state,
            reason="An ESTABLISHED_TREND implies persistent directional imbalance, but "
                   "balance_state reads IN_BALANCE (net-cancelling price action) over the same evidence window.",
        ))

    if trend_state in (taxonomy.TREND_ESTABLISHED, taxonomy.TREND_EMERGING) and swing_state == taxonomy.SWING_NO_DATA:
        contradictions.append(Contradiction(
            dimension_a="trend_state", value_a=trend_state,
            dimension_b="swing_state", value_b=swing_state,
            reason="A trend read requires at least two price-structure events, but swing_state reports "
                   "no swing data at all — trend was asserted on insufficient underlying evidence.",
        ))

    if compression_state == taxonomy.COMPRESSION_CONFIRMED and expansion_state == taxonomy.EXPANSION_CONFIRMED:
        contradictions.append(Contradiction(
            dimension_a="compression_state", value_a=compression_state,
            dimension_b="expansion_state", value_b=expansion_state,
            reason="Compression (contracting range) and Expansion (growing range) cannot both be "
                   "CONFIRMED over the same trailing evidence window — the magnitude-run derivation "
                   "disagrees with itself.",
        ))

    return tuple(contradictions)


# ---------------------------------------------------------------------------
# Deliverable 5 — structure_integrity: purely a function of
# contradiction count.
# ---------------------------------------------------------------------------
def derive_structure_integrity(contradictions: Tuple[Contradiction, ...]) -> str:
    if len(contradictions) == 0:
        return taxonomy.INTEGRITY_COHERENT
    if len(contradictions) == 1:
        return taxonomy.INTEGRITY_PARTIALLY_COHERENT
    return taxonomy.INTEGRITY_CONFLICTED


# ---------------------------------------------------------------------------
# Confidence — a function of evidence sufficiency AND contradiction
# count. Mirrors Series 77's confidence-decreases-with-contradiction
# property: for a fixed evidence count, confidence never increases as
# contradiction count increases.
# ---------------------------------------------------------------------------
def compute_confidence(evidence_count: int, contradiction_count: int) -> str:
    base_level = taxonomy.CONFIDENCE_NONE
    for threshold, level in _config.CONFIDENCE_EVIDENCE_THRESHOLDS:
        if evidence_count >= threshold:
            base_level = level
    base_rank = taxonomy.confidence_rank(base_level)
    penalized_rank = base_rank - contradiction_count
    return taxonomy.confidence_at_rank(penalized_rank)


# ---------------------------------------------------------------------------
# Deliverable 6 — Explanation, genuinely computed from the real
# reasoning trace.
# ---------------------------------------------------------------------------
def build_explanation(
    *,
    assessment_id: str,
    previous_assessment: Optional[PriceStructureAssessment],
    trend_state: str,
    swing_state: str,
    compression_state: str,
    expansion_state: str,
    balance_state: str,
    structure_state: str,
    contradictions: Tuple[Contradiction, ...],
    confidence: str,
    episode_ids: Tuple[str, ...],
    observation_ids: Tuple[str, ...],
    price_event_count: int,
) -> Explanation:
    why: List[str] = [
        f"structure_state={structure_state} derived from trend_state={trend_state} and balance_state={balance_state}.",
        f"trend_state={trend_state} derived from the trailing same-signed price-delta run.",
        f"swing_state={swing_state} derived from the presence/absence of a sign reversal in the price-delta sequence.",
        f"compression_state={compression_state}/expansion_state={expansion_state} derived (approximately, price-only) "
        f"from the trailing monotonic run of |price-delta| magnitudes.",
        f"balance_state={balance_state} derived from the ratio of net displacement to total absolute movement.",
    ]

    what_changed: Optional[str] = None
    if previous_assessment is not None and previous_assessment.assessment_id != assessment_id:
        changes = []
        for field in ("structure_state", "trend_state", "swing_state", "compression_state", "expansion_state", "balance_state"):
            old = getattr(previous_assessment, field)
            new = {"structure_state": structure_state, "trend_state": trend_state, "swing_state": swing_state,
                   "compression_state": compression_state, "expansion_state": expansion_state,
                   "balance_state": balance_state}[field]
            if old != new:
                changes.append(f"{field}: {old} -> {new}")
        what_changed = "; ".join(changes) if changes else "No dimension changed vs. previous assessment."

    missing_evidence: List[str] = [
        "compression_state/expansion_state are approximate: no real Volatility Structure (range/IV) data exists yet, "
        "only price-delta magnitude is used as a proxy.",
    ]
    if price_event_count < 2:
        missing_evidence.append("Fewer than 2 price-structure events available — swing/trend reads are structurally undetermined.")
    if swing_state == taxonomy.SWING_NO_DATA:
        missing_evidence.append("No confirmed swing yet — trend/correction conclusions cannot be fully evidenced.")

    would_increase_confidence: List[str] = []
    if contradictions:
        would_increase_confidence.append(f"Resolution of {len(contradictions)} contradiction(s) currently detected.")
    if price_event_count < 6:
        would_increase_confidence.append(f"{6 - price_event_count} additional price-structure event(s) reaching the {_config.CONFIDENCE_EVIDENCE_THRESHOLDS[-1][1]} evidence threshold.")
    if not would_increase_confidence:
        would_increase_confidence.append("No further evidence currently identified as increasing confidence beyond the present read.")

    return Explanation(
        assessment_id=assessment_id,
        what_changed=what_changed,
        why=tuple(why),
        which_episodes_caused_it=episode_ids,
        which_observations_support_it=observation_ids,
        missing_evidence=tuple(missing_evidence),
        would_increase_confidence=tuple(would_increase_confidence),
        schema_version=_config.SCHEMA_VERSION,
    )


# ---------------------------------------------------------------------------
# assessment_id — deterministic content hash. See models.py docstring.
# ---------------------------------------------------------------------------
def _assessment_id(
    episode_ids: Tuple[str, ...],
    structure_state: str,
    trend_state: str,
    swing_state: str,
    compression_state: str,
    expansion_state: str,
    balance_state: str,
    structure_integrity: str,
    confidence: str,
    schema_version: str,
    trend_direction_signal: Optional[str] = None,
) -> str:
    seed_parts = [
        "|".join(sorted(episode_ids)),
        structure_state, trend_state, swing_state,
        compression_state, expansion_state, balance_state,
        structure_integrity, confidence, schema_version,
        trend_direction_signal or "",
    ]
    seed = "||".join(seed_parts)
    return "PSA-" + hashlib.md5(seed.encode()).hexdigest()[:24]


# ---------------------------------------------------------------------------
# Top-level reasoning entrypoint — assembles all dimensions into one
# PriceStructureAssessment. Pure function of (episodes, events); no
# wall-clock/random state consulted anywhere in this module.
# ---------------------------------------------------------------------------
def assess_price_structure(
    episodes: Tuple[Episode, ...],
    events: Tuple[MarketEvent, ...],
    *,
    timestamp: str,
    previous_assessment: Optional[PriceStructureAssessment] = None,
    provenance: str = _config.DEFAULT_PROVENANCE,
    schema_version: str = _config.SCHEMA_VERSION,
) -> PriceStructureAssessment:
    events_by_id = {e.event_id: e for e in events}
    price_events = _price_events_in_order(episodes, events_by_id)

    trend_state = derive_trend_state(price_events)
    trend_direction_signal = derive_trend_direction_signal(price_events, trend_state)
    swing_state = derive_swing_state(price_events)
    compression_state = derive_compression_state(price_events)
    expansion_state = derive_expansion_state(price_events)
    balance_state = derive_balance_state(price_events)
    structure_state = derive_structure_state(trend_state, balance_state)

    contradictions = detect_contradictions(
        trend_state=trend_state, swing_state=swing_state, balance_state=balance_state,
        compression_state=compression_state, expansion_state=expansion_state,
    )
    structure_integrity = derive_structure_integrity(contradictions)
    confidence = compute_confidence(len(price_events), len(contradictions))

    episode_ids = tuple(sorted({ep.episode_id for ep in episodes}))
    event_ids = tuple(sorted({e.event_id for e in price_events}))
    observation_ids = tuple(sorted({oid for ep in episodes for oid in ep.originating_observation_ids}))

    assessment_id = _assessment_id(
        episode_ids, structure_state, trend_state, swing_state,
        compression_state, expansion_state, balance_state,
        structure_integrity, confidence, schema_version,
        trend_direction_signal,
    )

    explanation = build_explanation(
        assessment_id=assessment_id,
        previous_assessment=previous_assessment,
        trend_state=trend_state, swing_state=swing_state,
        compression_state=compression_state, expansion_state=expansion_state,
        balance_state=balance_state, structure_state=structure_state,
        contradictions=contradictions, confidence=confidence,
        episode_ids=episode_ids, observation_ids=observation_ids,
        price_event_count=len(price_events),
    )

    return PriceStructureAssessment(
        assessment_id=assessment_id,
        timestamp=timestamp,
        structure_state=structure_state,
        trend_state=trend_state,
        swing_state=swing_state,
        compression_state=compression_state,
        expansion_state=expansion_state,
        balance_state=balance_state,
        structure_integrity=structure_integrity,
        confidence=confidence,
        supporting_episode_ids=episode_ids,
        supporting_event_ids=event_ids,
        supporting_observation_ids=observation_ids,
        contradictions=contradictions,
        explanation=explanation,
        trend_direction_signal=trend_direction_signal,
        provenance=provenance,
        schema_version=schema_version,
    )
