"""Market Structure Intelligence engine — pure functions, no state, no IO.

Implements the Support & Resistance Intelligence domain (Deliverable 2,
domain 2) and Deliverable 1's Acceptance/Rejection/Auction concepts.
Answers WHERE price is located relative to structure — support,
resistance, breakout, breakdown, retest, rejection, structural balance
— orthogonal to `bujji.msi_price_structure.engine` (Series 78), which
answers HOW price is behaving (trend/swing/compression/expansion).
See taxonomy.py's module docstring for the full Step 0.6 overlap-check
disclosure.

Consumes the same `bujji.market_episode.models.Episode` (Series 76) /
`bujji.live_market_events.models.MarketEvent` (Series 75) objects Series
78 consumes, via the identical evidence-extraction convention
(`_price_events_in_order`), so both brains can run over the same raw
market data as siblings, not divergent designs.

Nothing here predicts direction, selects a strike, or scores trade
likelihood — purely descriptive interpretation of price's position
relative to structural levels.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from bujji.live_market_events.models import MarketEvent
from bujji.market_episode.models import Episode

from . import config as _config
from . import taxonomy
from .models import Contradiction, Explanation, MarketStructureAssessment


# ---------------------------------------------------------------------------
# Evidence extraction — identical membership/ordering convention to
# `bujji.msi_price_structure.engine._price_events_in_order`.
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


def _current_price(event: MarketEvent) -> Optional[float]:
    """PRICE_CHANGED/PRICE_GAP_DETECTED carry `detail.new_price`;
    NEW_SESSION_HIGH/NEW_SESSION_LOW carry `detail.new_value` — two
    genuinely different key names for the same "current price" concept
    across Series 75's own event-type families (confirmed by reading
    `bujji.live_market_events.engine` directly). Both are read here,
    never guessed."""
    value = event.detail.get("new_price", event.detail.get("new_value"))
    return float(value) if isinstance(value, (int, float)) else None


def _old_price(event: MarketEvent) -> Optional[float]:
    value = event.detail.get("old_price", event.detail.get("old_value"))
    return float(value) if isinstance(value, (int, float)) else None


def _prices_with_baseline(price_events: Tuple[MarketEvent, ...]) -> Tuple[float, ...]:
    """Prepend the FIRST event's `old_price`/`old_value` (the earliest
    known price the walk started from) ahead of every event's own
    `new_price`/`new_value`. Without this, the very first raw price a
    caller observed could never be recognized as a local extreme (it
    would always be missing a left neighbor) even when it genuinely was
    one — e.g. a spike-then-reversal pattern whose peak is the SECOND
    observed price. This mirrors treating the walk's true starting
    point as real evidence, never discarding it."""
    if not price_events:
        return ()
    out: List[float] = []
    baseline = _old_price(price_events[0])
    if baseline is not None:
        out.append(baseline)
    for event in price_events:
        value = _current_price(event)
        if value is not None:
            out.append(value)
    return tuple(out)


def _prices(price_events: Tuple[MarketEvent, ...]) -> Tuple[float, ...]:
    out: List[float] = []
    for event in price_events:
        value = _current_price(event)
        if value is not None:
            out.append(value)
    return tuple(out)


def _aligned_prices_and_events(
    price_events: Tuple[MarketEvent, ...],
) -> Tuple[Tuple[float, ...], Tuple[MarketEvent, ...]]:
    """Returns (prices, events) with events[i] the evidence reference
    for prices[i], INCLUDING the prepended baseline price (see
    `_prices_with_baseline`'s docstring) — the baseline point's own
    evidence reference is its first event (the earliest available
    evidence touching that region), never a fabricated id."""
    prices = _prices_with_baseline(price_events)
    if not prices:
        return (), ()
    if len(prices) == len(price_events) + 1:
        events = (price_events[0],) + price_events
    else:
        events = price_events
    return prices, events


def _dedup_consecutive_price_events(price_events: Tuple[MarketEvent, ...]) -> Tuple[MarketEvent, ...]:
    """A single underlying price move can emit MORE THAN ONE Series-75
    MarketEvent type (e.g. PRICE_CHANGED AND PRICE_GAP_DETECTED for the
    same old->new transition — "a gap is a large change, not a
    different kind of change", per `live_market_events.taxonomy`).
    Naively walking every event would duplicate the same price point
    consecutively, which breaks the strict-inequality local-extreme
    test below (a value is never strictly greater/less than an equal
    neighbor). Collapsing consecutive duplicate prices to their FIRST
    representative event is the structurally correct fix: level
    identification cares about the sequence of distinct prices reached,
    never about how many event *types* happened to fire for one move."""
    out: List[MarketEvent] = []
    last_price: Optional[float] = None
    for event in price_events:
        price = _current_price(event)
        if price is None:
            continue
        if out and last_price is not None and price == last_price:
            continue
        out.append(event)
        last_price = price
    return tuple(out)


def _within(price: float, level_price: float, fraction: float) -> bool:
    if level_price == 0:
        return price == 0
    return abs(price - level_price) / abs(level_price) <= fraction


# ---------------------------------------------------------------------------
# _Level — internal level-registry record. NOT a public model (never
# exported from models.py): a working structure engine.py builds and
# consumes for one call, mirroring how 78's engine keeps its own
# internal-only helpers (`_trailing_run_length`, etc.) out of models.py.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class _Level:
    level_type: str          # taxonomy.LEVEL_SUPPORT / LEVEL_RESISTANCE
    price: float
    formed_index: int
    formed_event_id: str
    test_count: int
    broken: bool
    break_index: Optional[int]
    break_event_id: Optional[str]
    break_confirmed: bool
    break_failed: bool
    retest_state: str        # taxonomy.RETEST_*


# ---------------------------------------------------------------------------
# Deliverable 2, domain 2 — level identification. "Identify where the
# market has previously demonstrated acceptance or rejection ... a
# ranked set of active levels, each tagged with strength/age/test
# count" (MSI_V1_FOUNDATION.md). A local extreme in the ordered price
# sequence (strictly higher/lower than both immediate neighbors) is the
# minimal, disclosed, price-only swing-point primitive available
# without real volume/OI participation data — necessarily approximate,
# same posture as Series 78's compression/expansion proxy.
# ---------------------------------------------------------------------------
def identify_structural_levels(
    prices: Tuple[float, ...],
    price_events: Tuple[MarketEvent, ...],
) -> Tuple[_Level, ...]:
    n = len(prices)
    if n < 3:
        return ()

    levels: List[_Level] = []
    for i in range(1, n - 1):
        is_local_max = prices[i] > prices[i - 1] and prices[i] > prices[i + 1]
        is_local_min = prices[i] < prices[i - 1] and prices[i] < prices[i + 1]
        if not (is_local_max or is_local_min):
            continue
        level_type = taxonomy.LEVEL_RESISTANCE if is_local_max else taxonomy.LEVEL_SUPPORT
        level_price = prices[i]

        test_count = 0
        broken = False
        break_index: Optional[int] = None
        break_confirmed = False
        break_failed = False
        retest_state = taxonomy.RETEST_NONE
        sustain_count = 0

        for j in range(i + 1, n):
            p = prices[j]
            if not broken:
                # A decisive break: price closes beyond the level by
                # more than the disclosed fixed fraction, in the
                # structurally meaningful direction (up through
                # resistance, down through support).
                decisive = abs(p - level_price) / abs(level_price) if level_price != 0 else abs(p - level_price)
                if level_type == taxonomy.LEVEL_RESISTANCE and p > level_price and decisive >= _config.BREAK_DECISIVE_FRACTION:
                    broken = True
                    break_index = j
                elif level_type == taxonomy.LEVEL_SUPPORT and p < level_price and decisive >= _config.BREAK_DECISIVE_FRACTION:
                    broken = True
                    break_index = j
                elif _within(p, level_price, _config.LEVEL_PROXIMITY_FRACTION):
                    test_count += 1
            else:
                # Post-break: track sustain (confirm), retest (price
                # returns to within proximity of the broken level from
                # the far/new side), and reversion (price moves clearly
                # back onto the PRE-break side, beyond the proximity
                # band — the level did not hold).
                near = _within(p, level_price, _config.RETEST_PROXIMITY_FRACTION)
                clearly_reverted = (
                    p < level_price * (1 - _config.RETEST_PROXIMITY_FRACTION)
                    if level_type == taxonomy.LEVEL_RESISTANCE
                    else p > level_price * (1 + _config.RETEST_PROXIMITY_FRACTION)
                )
                still_beyond_clear = (
                    p > level_price * (1 + _config.RETEST_PROXIMITY_FRACTION)
                    if level_type == taxonomy.LEVEL_RESISTANCE
                    else p < level_price * (1 - _config.RETEST_PROXIMITY_FRACTION)
                )

                if clearly_reverted:
                    if retest_state == taxonomy.RETEST_ACTIVE:
                        retest_state = taxonomy.RETEST_FAILED
                    else:
                        break_failed = True
                elif near:
                    if retest_state == taxonomy.RETEST_NONE:
                        retest_state = taxonomy.RETEST_ACTIVE
                elif still_beyond_clear:
                    sustain_count += 1
                    if sustain_count >= _config.BREAK_CONFIRM_MIN_SUSTAIN_EVENTS:
                        break_confirmed = True
                    if retest_state == taxonomy.RETEST_ACTIVE:
                        retest_state = taxonomy.RETEST_CONFIRMED

        formed_event_id = price_events[i].event_id
        levels.append(_Level(
            level_type=level_type, price=level_price,
            formed_index=i, formed_event_id=formed_event_id,
            test_count=test_count, broken=broken,
            break_index=break_index,
            break_event_id=price_events[break_index].event_id if break_index is not None else None,
            break_confirmed=break_confirmed, break_failed=break_failed,
            retest_state=retest_state,
        ))
    return tuple(levels)


def _nearest_level(levels: Tuple[_Level, ...], level_type: str, current_price: float, *, unbroken_only: bool) -> Optional[_Level]:
    candidates = [lv for lv in levels if lv.level_type == level_type and (not unbroken_only or not lv.broken)]
    if not candidates:
        return None
    return min(candidates, key=lambda lv: abs(lv.price - current_price))


# ---------------------------------------------------------------------------
# Deliverable 2 — support_state / resistance_state: a function of the
# nearest LIVE (unbroken) level's own test count only.
# ---------------------------------------------------------------------------
def derive_support_state(levels: Tuple[_Level, ...], current_price: float) -> str:
    level = _nearest_level(levels, taxonomy.LEVEL_SUPPORT, current_price, unbroken_only=True)
    if level is None:
        return taxonomy.SUPPORT_NONE
    if level.test_count >= _config.SUPPORT_ESTABLISHED_MIN_TESTS:
        return taxonomy.SUPPORT_ESTABLISHED
    if level.test_count >= _config.SUPPORT_DEVELOPING_MIN_TESTS:
        return taxonomy.SUPPORT_DEVELOPING
    if level.test_count >= _config.SUPPORT_WEAK_MIN_TESTS:
        return taxonomy.SUPPORT_WEAK
    return taxonomy.SUPPORT_NONE


def derive_resistance_state(levels: Tuple[_Level, ...], current_price: float) -> str:
    level = _nearest_level(levels, taxonomy.LEVEL_RESISTANCE, current_price, unbroken_only=True)
    if level is None:
        return taxonomy.RESISTANCE_NONE
    if level.test_count >= _config.RESISTANCE_ESTABLISHED_MIN_TESTS:
        return taxonomy.RESISTANCE_ESTABLISHED
    if level.test_count >= _config.RESISTANCE_DEVELOPING_MIN_TESTS:
        return taxonomy.RESISTANCE_DEVELOPING
    if level.test_count >= _config.RESISTANCE_WEAK_MIN_TESTS:
        return taxonomy.RESISTANCE_WEAK
    return taxonomy.RESISTANCE_NONE


# ---------------------------------------------------------------------------
# Deliverable 2 — breakout_state / breakdown_state: the MOST RECENT
# break (by formed_index) of the respective level type governs the
# read (a later, unbroken level does not erase evidence of an earlier
# genuine break).
# ---------------------------------------------------------------------------
def _latest_break(levels: Tuple[_Level, ...], level_type: str) -> Optional[_Level]:
    broken = [lv for lv in levels if lv.level_type == level_type and lv.broken]
    if not broken:
        return None
    return max(broken, key=lambda lv: lv.break_index)


def derive_breakout_state(levels: Tuple[_Level, ...]) -> str:
    level = _latest_break(levels, taxonomy.LEVEL_RESISTANCE)
    if level is None:
        return taxonomy.BREAKOUT_NONE
    if level.break_failed:
        return taxonomy.BREAKOUT_FAILED
    if level.break_confirmed:
        return taxonomy.BREAKOUT_CONFIRMED
    return taxonomy.BREAKOUT_DEVELOPING


def derive_breakdown_state(levels: Tuple[_Level, ...]) -> str:
    level = _latest_break(levels, taxonomy.LEVEL_SUPPORT)
    if level is None:
        return taxonomy.BREAKDOWN_NONE
    if level.break_failed:
        return taxonomy.BREAKDOWN_FAILED
    if level.break_confirmed:
        return taxonomy.BREAKDOWN_CONFIRMED
    return taxonomy.BREAKDOWN_DEVELOPING


# ---------------------------------------------------------------------------
# Deliverable 2 — retest_state: the most recently broken level's own
# retest_state field, across both level types (whichever break is more
# recent by formed_index).
# ---------------------------------------------------------------------------
def derive_retest_state(levels: Tuple[_Level, ...]) -> str:
    broken = [lv for lv in levels if lv.broken]
    if not broken:
        return taxonomy.RETEST_NONE
    latest = max(broken, key=lambda lv: lv.break_index)
    return latest.retest_state


# ---------------------------------------------------------------------------
# Deliverable 1 — Rejection: "price visiting a level and being quickly,
# forcefully returned from it ... indicating disagreement rather than
# acceptance." A test of an UNBROKEN level (the level held) is
# rejection evidence for that level. WEAK = exactly one test recorded
# on the strongest live level; STRONG = 2+ (the level has repeatedly
# turned price away without ever being accepted through).
# ---------------------------------------------------------------------------
def derive_rejection_state(levels: Tuple[_Level, ...]) -> str:
    unbroken = [lv for lv in levels if not lv.broken and lv.test_count > 0]
    if not unbroken:
        return taxonomy.REJECTION_NONE
    max_tests = max(lv.test_count for lv in unbroken)
    if max_tests >= 2:
        return taxonomy.REJECTION_STRONG
    return taxonomy.REJECTION_WEAK


# ---------------------------------------------------------------------------
# Deliverable 2 — structural_balance: is current price presently
# contained inside a recognized range bounded by an ESTABLISHED support
# AND an ESTABLISHED resistance level (both unbroken)? See taxonomy.py
# module docstring for why this is scoped differently from 78's
# balance_state.
# ---------------------------------------------------------------------------
def derive_structural_balance(
    levels: Tuple[_Level, ...],
    current_price: float,
    support_state: str,
    resistance_state: str,
) -> str:
    if not levels:
        return taxonomy.STRUCTURAL_BALANCE_UNKNOWN
    support = _nearest_level(levels, taxonomy.LEVEL_SUPPORT, current_price, unbroken_only=True)
    resistance = _nearest_level(levels, taxonomy.LEVEL_RESISTANCE, current_price, unbroken_only=True)
    if (
        support is not None and resistance is not None
        and support_state == taxonomy.SUPPORT_ESTABLISHED and resistance_state == taxonomy.RESISTANCE_ESTABLISHED
        and support.price <= current_price <= resistance.price
    ):
        return taxonomy.STRUCTURAL_BALANCE_RANGE_BOUND
    return taxonomy.STRUCTURAL_BALANCE_UNBOUNDED


# ---------------------------------------------------------------------------
# Deliverable 2 — structure_location: the composite/overall read,
# deterministically derived from the independent dimensions above plus
# current price's position relative to the level registry — mirrors
# Series 78's structure_state composite-derivation pattern.
# ---------------------------------------------------------------------------
def derive_structure_location(
    *,
    levels: Tuple[_Level, ...],
    current_price: float,
    support_state: str,
    resistance_state: str,
    breakout_state: str,
    breakdown_state: str,
    retest_state: str,
    structural_balance: str,
) -> str:
    if retest_state == taxonomy.RETEST_ACTIVE:
        return taxonomy.LOCATION_AT_RETEST
    if breakout_state == taxonomy.BREAKOUT_CONFIRMED:
        return taxonomy.LOCATION_ABOVE_RESISTANCE
    if breakdown_state == taxonomy.BREAKDOWN_CONFIRMED:
        return taxonomy.LOCATION_BELOW_SUPPORT
    if structural_balance == taxonomy.STRUCTURAL_BALANCE_RANGE_BOUND:
        return taxonomy.LOCATION_INSIDE_RANGE
    resistance = _nearest_level(levels, taxonomy.LEVEL_RESISTANCE, current_price, unbroken_only=True)
    support = _nearest_level(levels, taxonomy.LEVEL_SUPPORT, current_price, unbroken_only=True)
    if resistance is not None and _within(current_price, resistance.price, _config.LEVEL_PROXIMITY_FRACTION):
        return taxonomy.LOCATION_NEAR_RESISTANCE
    if support is not None and _within(current_price, support.price, _config.LEVEL_PROXIMITY_FRACTION):
        return taxonomy.LOCATION_NEAR_SUPPORT
    if support_state == taxonomy.SUPPORT_NONE and resistance_state == taxonomy.RESISTANCE_NONE:
        return taxonomy.LOCATION_UNKNOWN
    return taxonomy.LOCATION_UNKNOWN


# ---------------------------------------------------------------------------
# Deliverable 5 — contradiction detection. A REAL, structurally-sound
# case (not a forced one): a CONFIRMED breakout/breakdown asserts the
# level should now hold in its new role, but a FAILED retest of that
# same event is direct evidence it did NOT hold — the two reads
# disagree about the same underlying level. Mirrored for breakdown.
# Also: a support/resistance level cannot read ESTABLISHED (a live,
# structurally significant level) while that SAME dimension's break
# counterpart has CONFIRMED — a confirmed break means the level was
# decisively defeated, so it cannot simultaneously be reported as the
# nearest live, established level in the opposite direction check.
# ---------------------------------------------------------------------------
def detect_contradictions(
    *,
    support_state: str,
    resistance_state: str,
    breakout_state: str,
    breakdown_state: str,
    retest_state: str,
) -> Tuple[Contradiction, ...]:
    contradictions: List[Contradiction] = []

    if breakout_state == taxonomy.BREAKOUT_CONFIRMED and retest_state == taxonomy.RETEST_FAILED:
        contradictions.append(Contradiction(
            dimension_a="breakout_state", value_a=breakout_state,
            dimension_b="retest_state", value_b=retest_state,
            reason="A CONFIRMED breakout implies the broken resistance level should now hold as "
                   "support on retest, but retest_state reports FAILED — price crossed back through "
                   "the same level, direct evidence the breakout did not structurally hold.",
        ))

    if breakdown_state == taxonomy.BREAKDOWN_CONFIRMED and retest_state == taxonomy.RETEST_FAILED:
        contradictions.append(Contradiction(
            dimension_a="breakdown_state", value_a=breakdown_state,
            dimension_b="retest_state", value_b=retest_state,
            reason="A CONFIRMED breakdown implies the broken support level should now hold as "
                   "resistance on retest, but retest_state reports FAILED — price crossed back "
                   "through the same level, direct evidence the breakdown did not structurally hold.",
        ))

    if resistance_state == taxonomy.RESISTANCE_ESTABLISHED and breakout_state == taxonomy.BREAKOUT_CONFIRMED:
        contradictions.append(Contradiction(
            dimension_a="resistance_state", value_a=resistance_state,
            dimension_b="breakout_state", value_b=breakout_state,
            reason="resistance_state reports the nearest live resistance as ESTABLISHED (still "
                   "unbroken and structurally significant), but breakout_state reports a CONFIRMED "
                   "break has already occurred over the same evidence window — a decisively broken "
                   "level cannot simultaneously be the nearest live, established one.",
        ))

    if support_state == taxonomy.SUPPORT_ESTABLISHED and breakdown_state == taxonomy.BREAKDOWN_CONFIRMED:
        contradictions.append(Contradiction(
            dimension_a="support_state", value_a=support_state,
            dimension_b="breakdown_state", value_b=breakdown_state,
            reason="support_state reports the nearest live support as ESTABLISHED (still unbroken "
                   "and structurally significant), but breakdown_state reports a CONFIRMED break has "
                   "already occurred over the same evidence window — a decisively broken level "
                   "cannot simultaneously be the nearest live, established one.",
        ))

    return tuple(contradictions)


def compute_confidence(evidence_count: int, contradiction_count: int) -> str:
    base_level = taxonomy.CONFIDENCE_NONE
    for threshold, level in _config.CONFIDENCE_EVIDENCE_THRESHOLDS:
        if evidence_count >= threshold:
            base_level = level
    base_rank = taxonomy.confidence_rank(base_level)
    penalized_rank = base_rank - contradiction_count
    return taxonomy.confidence_at_rank(penalized_rank)


def build_explanation(
    *,
    assessment_id: str,
    previous_assessment: Optional[MarketStructureAssessment],
    structure_location: str,
    support_state: str,
    resistance_state: str,
    breakout_state: str,
    breakdown_state: str,
    retest_state: str,
    rejection_state: str,
    structural_balance: str,
    contradictions: Tuple[Contradiction, ...],
    confidence: str,
    episode_ids: Tuple[str, ...],
    observation_ids: Tuple[str, ...],
    level_count: int,
    price_event_count: int,
) -> Explanation:
    why: List[str] = [
        f"structure_location={structure_location} derived from support_state={support_state}, "
        f"resistance_state={resistance_state}, breakout_state={breakout_state}, "
        f"breakdown_state={breakdown_state}, retest_state={retest_state}, "
        f"structural_balance={structural_balance}, and current price's proximity to the level registry.",
        f"support_state={support_state}/resistance_state={resistance_state} derived from the nearest "
        f"live (unbroken) level's test count.",
        f"breakout_state={breakout_state}/breakdown_state={breakdown_state} derived from whether the "
        f"most recent break sustained (CONFIRMED), reverted (FAILED), or is still recent (DEVELOPING).",
        f"retest_state={retest_state} derived from whether price returned to, then held or crossed "
        f"back through, the most recently broken level.",
        f"rejection_state={rejection_state} derived from repeated forceful reversals off an unbroken level.",
        f"structural_balance={structural_balance} derived from whether price sits inside an "
        f"established-support/established-resistance range.",
    ]

    what_changed: Optional[str] = None
    if previous_assessment is not None and previous_assessment.assessment_id != assessment_id:
        current = {
            "structure_location": structure_location, "support_state": support_state,
            "resistance_state": resistance_state, "breakout_state": breakout_state,
            "breakdown_state": breakdown_state, "retest_state": retest_state,
            "rejection_state": rejection_state, "structural_balance": structural_balance,
        }
        changes = []
        for field, new in current.items():
            old = getattr(previous_assessment, field)
            if old != new:
                changes.append(f"{field}: {old} -> {new}")
        what_changed = "; ".join(changes) if changes else "No dimension changed vs. previous assessment."

    missing_evidence: List[str] = [
        "Level identification is approximate: no real volume/OI participation evidence exists yet "
        "(Options Structure/Futures Structure/Liquidity brains are not built), so local price extremes "
        "are the only available proxy for acceptance/rejection at a level.",
    ]
    if price_event_count < 3:
        missing_evidence.append("Fewer than 3 price-structure events available — level identification is structurally undetermined.")
    if level_count == 0:
        missing_evidence.append("No structural levels identified yet from the available price sequence.")

    would_increase_confidence: List[str] = []
    if contradictions:
        would_increase_confidence.append(f"Resolution of {len(contradictions)} contradiction(s) currently detected.")
    if level_count < 3:
        would_increase_confidence.append(f"{3 - level_count} additional identified structural level(s) reaching the {_config.CONFIDENCE_EVIDENCE_THRESHOLDS[-1][1]} evidence threshold.")
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


def _assessment_id(
    episode_ids: Tuple[str, ...],
    structure_location: str,
    support_state: str,
    resistance_state: str,
    breakout_state: str,
    breakdown_state: str,
    retest_state: str,
    rejection_state: str,
    structural_balance: str,
    confidence: str,
    schema_version: str,
) -> str:
    seed_parts = [
        "|".join(sorted(episode_ids)),
        structure_location, support_state, resistance_state,
        breakout_state, breakdown_state, retest_state,
        rejection_state, structural_balance, confidence, schema_version,
    ]
    seed = "||".join(seed_parts)
    return "MSA-" + hashlib.md5(seed.encode()).hexdigest()[:24]


# ---------------------------------------------------------------------------
# Top-level reasoning entrypoint. Pure function of (episodes, events);
# no wall-clock/random state consulted anywhere in this module. No
# time-advance entrypoint exists in this package at all (see
# taxonomy.py's transition-table comment) — structurally enforces
# "transitions must be evidence-driven, never time-driven."
# ---------------------------------------------------------------------------
def assess_market_structure(
    episodes: Tuple[Episode, ...],
    events: Tuple[MarketEvent, ...],
    *,
    timestamp: str,
    previous_assessment: Optional[MarketStructureAssessment] = None,
    provenance: str = _config.DEFAULT_PROVENANCE,
    schema_version: str = _config.SCHEMA_VERSION,
) -> MarketStructureAssessment:
    events_by_id = {e.event_id: e for e in events}
    price_events = _price_events_in_order(episodes, events_by_id)
    deduped_price_events = _dedup_consecutive_price_events(price_events)
    prices, aligned_events = _aligned_prices_and_events(deduped_price_events)

    levels = identify_structural_levels(prices, aligned_events)
    current_price = prices[-1] if prices else 0.0

    support_state = derive_support_state(levels, current_price)
    resistance_state = derive_resistance_state(levels, current_price)
    breakout_state = derive_breakout_state(levels)
    breakdown_state = derive_breakdown_state(levels)
    retest_state = derive_retest_state(levels)
    rejection_state = derive_rejection_state(levels)
    structural_balance = derive_structural_balance(levels, current_price, support_state, resistance_state)
    structure_location = derive_structure_location(
        levels=levels, current_price=current_price,
        support_state=support_state, resistance_state=resistance_state,
        breakout_state=breakout_state, breakdown_state=breakdown_state,
        retest_state=retest_state, structural_balance=structural_balance,
    )

    contradictions = detect_contradictions(
        support_state=support_state, resistance_state=resistance_state,
        breakout_state=breakout_state, breakdown_state=breakdown_state,
        retest_state=retest_state,
    )
    confidence = compute_confidence(len(levels), len(contradictions))

    episode_ids = tuple(sorted({ep.episode_id for ep in episodes}))
    event_ids = tuple(sorted({e.event_id for e in price_events}))
    observation_ids = tuple(sorted({oid for ep in episodes for oid in ep.originating_observation_ids}))

    assessment_id = _assessment_id(
        episode_ids, structure_location, support_state, resistance_state,
        breakout_state, breakdown_state, retest_state, rejection_state,
        structural_balance, confidence, schema_version,
    )

    explanation = build_explanation(
        assessment_id=assessment_id,
        previous_assessment=previous_assessment,
        structure_location=structure_location, support_state=support_state,
        resistance_state=resistance_state, breakout_state=breakout_state,
        breakdown_state=breakdown_state, retest_state=retest_state,
        rejection_state=rejection_state, structural_balance=structural_balance,
        contradictions=contradictions, confidence=confidence,
        episode_ids=episode_ids, observation_ids=observation_ids,
        level_count=len(levels), price_event_count=len(price_events),
    )

    return MarketStructureAssessment(
        assessment_id=assessment_id,
        timestamp=timestamp,
        structure_location=structure_location,
        support_state=support_state,
        resistance_state=resistance_state,
        breakout_state=breakout_state,
        breakdown_state=breakdown_state,
        retest_state=retest_state,
        rejection_state=rejection_state,
        structural_balance=structural_balance,
        confidence=confidence,
        supporting_episode_ids=episode_ids,
        supporting_event_ids=event_ids,
        supporting_observation_ids=observation_ids,
        contradictions=contradictions,
        explanation=explanation,
        provenance=provenance,
        schema_version=schema_version,
    )
