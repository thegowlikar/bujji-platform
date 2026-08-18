"""Position Intelligence Engine — pure functions, no state, no IO, no
broker, no execution. Answers, for a hypothetical position: "is the
reason I entered still valid, given what's happened since?"

Every check is grounded in already-real, already-computed fields
(ShadowTradeCandidate at entry, a later real intelligence_cycle record)
-- never a fabricated price, never hindsight leaking backward (the
caller supplies which later record to check against; this module never
looks anywhere else). `recommendation` is advisory text only -- this
module never HOLDs/EXITs/ADJUSTs anything; nothing here can reach a
broker or PaperBroker.
"""
from __future__ import annotations

from typing import Optional, Tuple

from .models import (
    CHECK_CONSISTENT, CHECK_DEVIATED, CHECK_UNKNOWN,
    RECOMMEND_EXIT, RECOMMEND_HOLD, RECOMMEND_UNKNOWN,
    THESIS_INTACT, THESIS_INVALIDATED, THESIS_UNKNOWN, THESIS_WEAKENING,
    PositionEntrySnapshot, ThesisCheck, ThesisEvaluation,
)

_BULLISH_BAND = frozenset({"STRONG_BULLISH", "BULLISH", "WEAK_BULLISH"})
_BEARISH_BAND = frozenset({"STRONG_BEARISH", "BEARISH", "WEAK_BEARISH"})

_DIRECTION_SENSITIVE_FAMILIES = frozenset({"LONG_DIRECTIONAL", "SHORT_DIRECTIONAL", "RATIO", "SYNTHETIC", "COVERED"})
_EXPANSION_SEEKING_FAMILIES = frozenset({"VOLATILITY_EXPANSION", "NEUTRAL_PREMIUM_BUYING"})
_COMPRESSION_SEEKING_FAMILIES = frozenset({"VOLATILITY_COMPRESSION", "NEUTRAL_PREMIUM_SELLING", "IRON_CONDOR", "IRON_FLY", "BUTTERFLY"})
_RANGE_DEPENDENT_FAMILIES = frozenset({"NEUTRAL_PREMIUM_SELLING", "IRON_CONDOR", "IRON_FLY", "BUTTERFLY", "VOLATILITY_COMPRESSION"})


def _safe_get(d, *path, default=None):
    cur = d
    for k in path:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k)
    return default if cur is None else cur


def _direction_band(direction: Optional[str]) -> Optional[str]:
    if direction in _BULLISH_BAND:
        return "BULLISH"
    if direction in _BEARISH_BAND:
        return "BEARISH"
    if direction == "NEUTRAL":
        return "NEUTRAL"
    return None  # UNKNOWN or MIXED -- no real band.


def build_entry_snapshot(candidate, source_intelligence_cycle_record: Optional[dict] = None) -> PositionEntrySnapshot:
    """`candidate`: a real `ShadowTradeCandidate` (Phase 14). Captures
    exactly its own real fields -- nothing re-derived.

    Phase 15F: `source_intelligence_cycle_record` -- the SAME real
    intelligence_cycle record `candidate` was constructed from (its
    "greeks"/"premium_behaviour" fields, Phase 15E). Optional, defaults
    to None -- omitting it (or passing a record from before Phase 15E
    existed) yields `entry_greeks=None`/`entry_premium_behaviour=None`,
    which every Phase 15F check below treats as honest UNKNOWN, never
    a fabricated baseline."""
    entry_greeks = None
    entry_premium_behaviour = None
    if source_intelligence_cycle_record is not None:
        entry_greeks = source_intelligence_cycle_record.get("greeks")
        entry_premium_behaviour = source_intelligence_cycle_record.get("premium_behaviour")
    return PositionEntrySnapshot(
        candidate_id=candidate.candidate_id,
        strategy_family=candidate.strategy_family,
        entry_timestamp=candidate.timestamp,
        entry_regime=candidate.market_regime,
        entry_direction=candidate.direction,
        entry_volatility_regime=None,  # ShadowTradeCandidate does not carry this today -- honestly None.
        entry_consensus_state=candidate.consensus_state,
        entry_liquidity_tightness=None,  # not carried by ShadowTradeCandidate today -- honestly None.
        entry_greeks=entry_greeks,
        entry_premium_behaviour=entry_premium_behaviour,
    )


def _check_direction(family: str, entry_direction: Optional[str], current_direction: Optional[str]) -> Optional[ThesisCheck]:
    if family not in _DIRECTION_SENSITIVE_FAMILIES:
        return None
    entry_band = _direction_band(entry_direction)
    if entry_band is None:
        return ThesisCheck("direction", entry_direction, current_direction, CHECK_UNKNOWN,
                            "entry direction itself was not a real directional band -- nothing to compare")
    current_band = _direction_band(current_direction)
    if current_band is None:
        return ThesisCheck("direction", entry_direction, current_direction, CHECK_UNKNOWN,
                            "current direction is unresolved -- cannot confirm or refute the entry thesis")
    if current_band == entry_band:
        return ThesisCheck("direction", entry_direction, current_direction, CHECK_CONSISTENT,
                            f"direction remains {current_band}, consistent with entry")
    return ThesisCheck("direction", entry_direction, current_direction, CHECK_DEVIATED,
                        f"direction moved from {entry_band} at entry to {current_band} now")


def _check_volatility_trend(family: str, entry_regime: Optional[str], current_regime: Optional[str]) -> Optional[ThesisCheck]:
    if family in _EXPANSION_SEEKING_FAMILIES:
        if current_regime is None or current_regime == "UNKNOWN":
            return ThesisCheck("volatility_trend", entry_regime, current_regime, CHECK_UNKNOWN,
                                "current volatility regime unresolved")
        if entry_regime == "COMPRESSED" and current_regime == "STABLE":
            return ThesisCheck("volatility_trend", entry_regime, current_regime, CHECK_DEVIATED,
                                "compression resolved to flat stability without ever expanding -- the anticipated move did not materialize")
        return ThesisCheck("volatility_trend", entry_regime, current_regime, CHECK_CONSISTENT,
                            f"volatility regime now {current_regime} -- still consistent with an expansion thesis")
    if family in _COMPRESSION_SEEKING_FAMILIES:
        if current_regime is None or current_regime == "UNKNOWN":
            return ThesisCheck("volatility_trend", entry_regime, current_regime, CHECK_UNKNOWN,
                                "current volatility regime unresolved")
        if current_regime == "HIGH_VOLATILITY":
            return ThesisCheck("volatility_trend", entry_regime, current_regime, CHECK_DEVIATED,
                                "volatility expanded sharply -- a clear break against a range/compression thesis")
        return ThesisCheck("volatility_trend", entry_regime, current_regime, CHECK_CONSISTENT,
                            f"volatility regime now {current_regime} -- still consistent with a compression thesis")
    return None


def _check_regime_compatibility(family: str, entry_regime: Optional[str], current_regime: Optional[str]) -> Optional[ThesisCheck]:
    if family not in _RANGE_DEPENDENT_FAMILIES:
        return None
    if current_regime is None or current_regime == "UNKNOWN":
        return ThesisCheck("regime", entry_regime, current_regime, CHECK_UNKNOWN, "current regime unresolved")
    if current_regime == "TRENDING":
        return ThesisCheck("regime", entry_regime, current_regime, CHECK_DEVIATED,
                            "market broke into a trending regime -- a clear break against a range-dependent thesis")
    if current_regime in ("RANGING", "COMPRESSED"):
        return ThesisCheck("regime", entry_regime, current_regime, CHECK_CONSISTENT,
                            f"regime remains {current_regime} -- still range-bound, consistent with entry")
    return ThesisCheck("regime", entry_regime, current_regime, CHECK_UNKNOWN, f"regime={current_regime} -- inconclusive for this thesis")


# --- Phase 15F: Greeks + Premium Behaviour evidence -------------------
#
# Ownership boundary (explicit, per Phase 15F Step 2): these checks
# NEVER recompute direction/volatility/regime themselves -- they only
# read the ALREADY-PUBLISHED "greeks"/"premium_behaviour" fields a real
# intelligence_cycle record carries (Phase 15E), exactly the same way
# the three checks above only read "market_direction"/"volatility_structure"/
# "market_state", never re-deriving them. Position Intelligence's own
# job stays singular: "does the entry thesis still make sense," never
# "what is the market doing" (that's MDI/VSB/MPPI's job).

_NEUTRAL_DELTA_BIAS_BAND = 0.05  # |net delta bias| below this is too close to ATM-neutral to call a direction either way.


def _net_delta_bias(greeks: Optional[dict]) -> Optional[float]:
    """delta_ce + delta_pe -- via put-call parity (delta_ce - delta_pe
    == 1 for the same strike/expiry), this equals 2*delta_ce - 1: a
    real, well-defined moneyness/direction proxy (positive = spot above
    the ATM strike, negative = below), not a fabricated combination.
    None when either leg's Greeks are unavailable -- never guessed."""
    if not greeks:
        return None
    ce, pe = greeks.get("ce") or {}, greeks.get("pe") or {}
    if not ce.get("available") or not pe.get("available"):
        return None
    delta_ce, delta_pe = ce.get("delta"), pe.get("delta")
    if delta_ce is None or delta_pe is None:
        return None
    return delta_ce + delta_pe


def _check_delta_exposure(
    family: str, entry_direction: Optional[str], entry_greeks: Optional[dict], current_greeks: Optional[dict],
) -> Optional[ThesisCheck]:
    if family not in _DIRECTION_SENSITIVE_FAMILIES:
        return None
    entry_bias = _net_delta_bias(entry_greeks)
    current_bias = _net_delta_bias(current_greeks)
    if entry_bias is None or current_bias is None:
        return ThesisCheck("delta_exposure", entry_bias, current_bias, CHECK_UNKNOWN,
                            "Greeks unavailable at entry and/or now -- cannot compare delta exposure")
    if abs(entry_bias) < _NEUTRAL_DELTA_BIAS_BAND or abs(current_bias) < _NEUTRAL_DELTA_BIAS_BAND:
        return ThesisCheck("delta_exposure", round(entry_bias, 4), round(current_bias, 4), CHECK_UNKNOWN,
                            "net delta bias too close to neutral to call a directional lean either way")
    if (entry_bias > 0) == (current_bias > 0):
        return ThesisCheck("delta_exposure", round(entry_bias, 4), round(current_bias, 4), CHECK_CONSISTENT,
                            "net ATM delta bias still points the same direction as at entry")
    return ThesisCheck("delta_exposure", round(entry_bias, 4), round(current_bias, 4), CHECK_DEVIATED,
                        "net ATM delta bias has flipped direction since entry")


def _check_premium_direction_confirmation(
    family: str, current_direction: Optional[str], current_premium_behaviour: Optional[dict],
) -> Optional[ThesisCheck]:
    if family not in _DIRECTION_SENSITIVE_FAMILIES:
        return None
    band = _direction_band(current_direction)
    if band is None or band == "NEUTRAL":
        return None  # No real directional read to confirm/refute against -- not this check's job to guess one.
    relative = _safe_get(current_premium_behaviour, "ce_vs_pe_relative")
    if relative is None or relative == "UNKNOWN":
        return ThesisCheck("premium_direction_confirmation", band, relative, CHECK_UNKNOWN,
                            "premium behaviour unavailable -- cannot confirm or refute price direction via premium response")
    if relative == "SYMMETRIC":
        return ThesisCheck("premium_direction_confirmation", band, relative, CHECK_UNKNOWN,
                            "CE and PE premiums are moving symmetrically -- premium is not distinctly confirming either direction")
    expected = "CE_EXPANDING_FASTER" if band == "BULLISH" else "PE_EXPANDING_FASTER"
    if relative == expected:
        return ThesisCheck("premium_direction_confirmation", band, relative, CHECK_CONSISTENT,
                            f"option premium is responding {band.lower()}, confirming the current price direction")
    return ThesisCheck("premium_direction_confirmation", band, relative, CHECK_DEVIATED,
                        f"option premium is responding opposite to the current {band.lower()} price direction")


def _check_premium_selling_pressure(family: str, current_premium_behaviour: Optional[dict]) -> Optional[ThesisCheck]:
    if family not in _COMPRESSION_SEEKING_FAMILIES:
        return None
    combined_direction = _safe_get(current_premium_behaviour, "combined", "direction")
    if combined_direction is None or combined_direction == "UNKNOWN":
        return ThesisCheck("premium_selling_pressure", None, combined_direction, CHECK_UNKNOWN,
                            "premium behaviour unavailable -- cannot assess pressure on this premium-selling thesis")
    if combined_direction == "RISING":
        acceleration = _safe_get(current_premium_behaviour, "combined", "acceleration")
        note = " and accelerating" if acceleration == "ACCELERATING" else ""
        return ThesisCheck("premium_selling_pressure", None, combined_direction, CHECK_DEVIATED,
                            f"combined premium is expanding{note} -- working against a premium-selling/compression thesis")
    return ThesisCheck("premium_selling_pressure", None, combined_direction, CHECK_CONSISTENT,
                        f"combined premium is {combined_direction.lower()} -- still consistent with a premium-selling/compression thesis")


def _evidence_confidence(checks: Tuple[ThesisCheck, ...]) -> str:
    if not checks:
        return "NONE"
    resolved = [c for c in checks if c.status != CHECK_UNKNOWN]
    fraction = len(resolved) / len(checks)
    if fraction == 0:
        return "NONE"
    if fraction < 0.4:
        return "LOW"
    if fraction < 0.75:
        return "MODERATE"
    return "HIGH"


def evaluate_thesis(entry: PositionEntrySnapshot, current_record: dict) -> ThesisEvaluation:
    """Pure function: an entry snapshot + a LATER real intelligence_cycle
    record (dict) -> one ThesisEvaluation. Never raises on missing
    fields; never invents a check for a family/dimension that genuinely
    has no relevant evidence."""
    current_direction = _safe_get(current_record, "market_direction", "overall_direction")
    current_vol_regime = _safe_get(current_record, "volatility_structure", "volatility_regime")
    current_regime = _safe_get(current_record, "market_state", "regime")
    current_greeks = current_record.get("greeks") if isinstance(current_record, dict) else None
    current_premium_behaviour = current_record.get("premium_behaviour") if isinstance(current_record, dict) else None

    checks = tuple(
        c for c in (
            _check_direction(entry.strategy_family, entry.entry_direction, current_direction),
            _check_volatility_trend(entry.strategy_family, entry.entry_volatility_regime, current_vol_regime),
            _check_regime_compatibility(entry.strategy_family, entry.entry_regime, current_regime),
            _check_delta_exposure(entry.strategy_family, entry.entry_direction, entry.entry_greeks, current_greeks),
            _check_premium_direction_confirmation(entry.strategy_family, current_direction, current_premium_behaviour),
            _check_premium_selling_pressure(entry.strategy_family, current_premium_behaviour),
        ) if c is not None
    )

    if not checks or all(c.status == CHECK_UNKNOWN for c in checks):
        thesis_status = THESIS_UNKNOWN
        recommendation, recommendation_reason = RECOMMEND_UNKNOWN, "insufficient evidence to evaluate this thesis"
    else:
        deviated = [c for c in checks if c.status == CHECK_DEVIATED]
        resolved = [c for c in checks if c.status != CHECK_UNKNOWN]
        if not deviated:
            thesis_status = THESIS_INTACT
            recommendation, recommendation_reason = RECOMMEND_HOLD, "all checkable dimensions remain consistent with the entry thesis"
        elif len(deviated) >= len(resolved):
            thesis_status = THESIS_INVALIDATED
            recommendation, recommendation_reason = RECOMMEND_EXIT, "; ".join(c.reason for c in deviated)
        else:
            thesis_status = THESIS_WEAKENING
            recommendation, recommendation_reason = RECOMMEND_HOLD, "some dimensions have deviated: " + "; ".join(c.reason for c in deviated)

    return ThesisEvaluation(
        candidate_id=entry.candidate_id,
        evaluation_timestamp=current_record.get("timestamp", ""),
        thesis_status=thesis_status,
        checks=checks,
        recommendation=recommendation,
        recommendation_reason=recommendation_reason,
        evidence_confidence=_evidence_confidence(checks),
    )
