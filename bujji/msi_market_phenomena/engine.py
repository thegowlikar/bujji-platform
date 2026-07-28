"""MPC engine — Series 103. Pure functions: no IO, no state, no
wall-clock reads, no randomness. Zero Production imports -- operates
ONLY on the plain, caller-supplied `MarketSnapshot` (a real translation
of real Intelligence-layer field values, built by translate.py, the
one file in this package that reaches into Production types).

Every phenomenon rule below is a declarative, disclosed boolean
condition over REAL fields -- never fit to replay outcomes, never
tuned, exactly this project's established MSI-package rule-table
convention (e.g. msi_strategy_selection_foundation's own
STRATEGY_DEFINITIONS)."""
from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from . import config as _config
from . import taxonomy
from .models import MarketPhenomenaReport, MarketSnapshot, Phenomenon

_RuleFn = Callable[[MarketSnapshot], Tuple[bool, Tuple[str, ...]]]


def _rule_trend_expansion(s: MarketSnapshot) -> Tuple[bool, Tuple[str, ...]]:
    matched = s.structure_state == "TRENDING" and s.vsb_expansion_state == "CONFIRMED"
    return matched, (f"structure_state={s.structure_state}, vsb_expansion_state={s.vsb_expansion_state}",)


def _rule_trend_failure(s: MarketSnapshot) -> Tuple[bool, Tuple[str, ...]]:
    matched = s.structure_state == "CORRECTING"
    return matched, (f"structure_state={s.structure_state}",)


def _rule_range_compression(s: MarketSnapshot) -> Tuple[bool, Tuple[str, ...]]:
    matched = s.compression_state == "CONFIRMED" or s.structural_balance == "RANGE_BOUND"
    return matched, (f"compression_state={s.compression_state}, structural_balance={s.structural_balance}",)


def _rule_volatility_expansion(s: MarketSnapshot) -> Tuple[bool, Tuple[str, ...]]:
    matched = s.vsb_expansion_state == "CONFIRMED"
    return matched, (f"vsb_expansion_state={s.vsb_expansion_state}",)


def _rule_volatility_compression(s: MarketSnapshot) -> Tuple[bool, Tuple[str, ...]]:
    matched = s.vsb_compression_state == "CONFIRMED"
    return matched, (f"vsb_compression_state={s.vsb_compression_state}",)


def _rule_momentum_persistence(s: MarketSnapshot) -> Tuple[bool, Tuple[str, ...]]:
    matched = s.structure_state == "TRENDING" and s.overall_direction not in ("UNKNOWN", "MIXED", "NEUTRAL")
    return matched, (f"structure_state={s.structure_state}, overall_direction={s.overall_direction}",)


def _rule_momentum_exhaustion(s: MarketSnapshot) -> Tuple[bool, Tuple[str, ...]]:
    matched = s.structure_state == "CORRECTING" and s.structure_location == "AT_RETEST"
    return matched, (f"structure_state={s.structure_state}, structure_location={s.structure_location}",)


def _rule_mean_reversion(s: MarketSnapshot) -> Tuple[bool, Tuple[str, ...]]:
    matched = s.structural_balance == "RANGE_BOUND" and s.structure_state == "BALANCE"
    return matched, (f"structural_balance={s.structural_balance}, structure_state={s.structure_state}",)


def _rule_gap_continuation(s: MarketSnapshot) -> Tuple[bool, Tuple[str, ...]]:
    if s.open_price is None or s.previous_close_price is None:
        return False, ("no real open/previous-close price available -- cannot evaluate a gap",)
    gap_up = s.open_price > s.previous_close_price
    gap_down = s.open_price < s.previous_close_price
    if not (gap_up or gap_down):
        return False, ("open_price == previous_close_price -- no real gap",)
    direction_agrees = (gap_up and s.overall_direction in ("BULLISH", "STRONG_BULLISH", "WEAK_BULLISH")) or \
                        (gap_down and s.overall_direction in ("BEARISH", "STRONG_BEARISH", "WEAK_BEARISH"))
    return direction_agrees, (f"open_price={s.open_price}, previous_close_price={s.previous_close_price}, "
                               f"overall_direction={s.overall_direction}",)


def _rule_gap_failure(s: MarketSnapshot) -> Tuple[bool, Tuple[str, ...]]:
    if s.open_price is None or s.previous_close_price is None:
        return False, ("no real open/previous-close price available -- cannot evaluate a gap",)
    gap_up = s.open_price > s.previous_close_price
    gap_down = s.open_price < s.previous_close_price
    if not (gap_up or gap_down):
        return False, ("open_price == previous_close_price -- no real gap",)
    direction_disagrees = (gap_up and s.overall_direction in ("BEARISH", "STRONG_BEARISH", "WEAK_BEARISH")) or \
                           (gap_down and s.overall_direction in ("BULLISH", "STRONG_BULLISH", "WEAK_BULLISH"))
    return direction_disagrees, (f"open_price={s.open_price}, previous_close_price={s.previous_close_price}, "
                                  f"overall_direction={s.overall_direction}",)


_PHENOMENON_RULES: Dict[str, _RuleFn] = {
    taxonomy.PHENOMENON_TREND_EXPANSION: _rule_trend_expansion,
    taxonomy.PHENOMENON_TREND_FAILURE: _rule_trend_failure,
    taxonomy.PHENOMENON_RANGE_COMPRESSION: _rule_range_compression,
    taxonomy.PHENOMENON_VOLATILITY_EXPANSION: _rule_volatility_expansion,
    taxonomy.PHENOMENON_VOLATILITY_COMPRESSION: _rule_volatility_compression,
    taxonomy.PHENOMENON_MOMENTUM_PERSISTENCE: _rule_momentum_persistence,
    taxonomy.PHENOMENON_MOMENTUM_EXHAUSTION: _rule_momentum_exhaustion,
    taxonomy.PHENOMENON_MEAN_REVERSION: _rule_mean_reversion,
    taxonomy.PHENOMENON_GAP_CONTINUATION: _rule_gap_continuation,
    taxonomy.PHENOMENON_GAP_FAILURE: _rule_gap_failure,
}

assert set(_PHENOMENON_RULES) == set(taxonomy.ALL_CLASSIFIABLE_PHENOMENA_V1)


def validate_causal_order(snapshots: Sequence[MarketSnapshot]) -> Tuple[bool, Tuple[str, ...]]:
    """The one, disclosed causality rule for this package: real snapshot
    timestamps must be strictly non-decreasing. A snapshot sequence
    presented out of real chronological order is rejected -- classifying
    against it would risk using a later snapshot's information to
    explain an earlier one (a hindsight label)."""
    violations = []
    for prev, cur in zip(snapshots, snapshots[1:]):
        if cur.timestamp < prev.timestamp:
            violations.append(f"{cur.timestamp} appears after {prev.timestamp} but sorts earlier -- out-of-order snapshot sequence")
    if violations:
        return False, tuple(violations)
    return True, (f"{len(snapshots)} real snapshot(s) in non-decreasing chronological order",)


def _duration_seconds(earliest: str, latest: str) -> Optional[float]:
    try:
        t0 = datetime.fromisoformat(earliest)
        t1 = datetime.fromisoformat(latest)
        return (t1 - t0).total_seconds()
    except ValueError:
        return None


def _phenomenon_id(phenomenon_type: str, day: str, earliest: str, latest: str, schema_version: str) -> str:
    content = "|".join([phenomenon_type, day, earliest, latest, schema_version])
    return "PH-" + hashlib.md5(content.encode("utf-8")).hexdigest()[:24]


def classify_day(
    day: str, snapshots: Sequence[MarketSnapshot], *, generated_timestamp: str,
    affected_instruments: Sequence[str] = ("NIFTY",), schema_version: str = _config.SCHEMA_VERSION,
) -> MarketPhenomenaReport:
    """Produces exactly one real MarketPhenomenaReport for one real day.
    Raises ValueError if the real snapshot sequence is not causally
    ordered -- classification never proceeds against an invalid
    sequence, per the mission's own causality requirement."""
    ok, order_reasons = validate_causal_order(snapshots)
    if not ok:
        raise ValueError(f"snapshots are not causally ordered: {order_reasons}")

    phenomena: List[Phenomenon] = []
    for phenomenon_type, rule in _PHENOMENON_RULES.items():
        matched_snapshots = []
        matched_reasoning: List[str] = []
        for s in snapshots:
            matched, reasoning = rule(s)
            if matched:
                matched_snapshots.append(s)
                matched_reasoning.extend(f"{s.timestamp}: {r}" for r in reasoning)
        if not matched_snapshots:
            continue

        earliest = matched_snapshots[0].timestamp
        latest = matched_snapshots[-1].timestamp
        match_ratio = len(matched_snapshots) / len(snapshots)
        if match_ratio == 1.0:
            confidence = taxonomy.CONFIDENCE_HIGH
        elif match_ratio > 0.5:
            confidence = taxonomy.CONFIDENCE_MODERATE
        else:
            confidence = taxonomy.CONFIDENCE_LOW

        evidence_ids = tuple(sorted({aid for s in matched_snapshots for aid in s.supporting_assessment_ids}))
        pid = _phenomenon_id(phenomenon_type, day, earliest, latest, schema_version)
        phenomena.append(Phenomenon(
            phenomenon_id=pid, phenomenon_type=phenomenon_type, day=day,
            earliest_detection_timestamp=earliest, latest_confirmation_timestamp=latest,
            supporting_observations=tuple(matched_reasoning), evidence_references=evidence_ids,
            confidence=confidence, duration_seconds=_duration_seconds(earliest, latest),
            affected_instruments=tuple(affected_instruments), schema_version=schema_version,
        ))

    report_id = "MPR-" + hashlib.md5("|".join([day, ",".join(p.phenomenon_id for p in phenomena), schema_version]).encode("utf-8")).hexdigest()[:24]
    return MarketPhenomenaReport(
        report_id=report_id, day=day, generated_timestamp=generated_timestamp,
        phenomena=tuple(phenomena), not_classifiable=taxonomy.ALL_NOT_CLASSIFIABLE_V1,
        not_classifiable_reason=taxonomy.NOT_CLASSIFIABLE_REASON, schema_version=schema_version,
    )
