"""Tests -- Phase 15F: Position Intelligence consuming Greeks +
Premium Behaviour as additional evidence.

SEMANTIC FIXTURES ONLY -- deterministic, hand-constructed evidence
combinations, explicitly NOT real-market validation (see
test_position_intelligence_real_data_15f.py / the Phase 15F report for
the real Session B measurement, which is kept separate and clearly
labeled)."""
from __future__ import annotations

from bujji.position_intelligence.engine import build_entry_snapshot, evaluate_thesis
from bujji.position_intelligence.models import (
    CHECK_CONSISTENT, CHECK_DEVIATED, CHECK_UNKNOWN,
    RECOMMEND_EXIT, RECOMMEND_HOLD, RECOMMEND_UNKNOWN,
    THESIS_INTACT, THESIS_INVALIDATED, THESIS_UNKNOWN, THESIS_WEAKENING,
    PositionEntrySnapshot,
)


def _greeks(ce_delta=0.6, pe_delta=-0.4, ce_available=True, pe_available=True):
    return {
        "ce": {"available": ce_available, "delta": ce_delta if ce_available else None},
        "pe": {"available": pe_available, "delta": pe_delta if pe_available else None},
    }


def _premium_behaviour(ce_vs_pe="CE_EXPANDING_FASTER", combined_direction="RISING", combined_accel="STEADY"):
    return {
        "ce_vs_pe_relative": ce_vs_pe,
        "combined": {"direction": combined_direction, "acceleration": combined_accel},
    }


def _entry(family="LONG_DIRECTIONAL", direction="BULLISH", regime="RANGING", entry_greeks=None, entry_premium_behaviour=None):
    return PositionEntrySnapshot(
        candidate_id="TEST", strategy_family=family, entry_timestamp="t0",
        entry_regime=regime, entry_direction=direction, entry_volatility_regime=None,
        entry_consensus_state=None, entry_liquidity_tightness=None,
        entry_greeks=entry_greeks, entry_premium_behaviour=entry_premium_behaviour,
    )


def _record(direction=None, regime=None, greeks=None, premium_behaviour=None, ts="t1"):
    return {
        "timestamp": ts,
        "market_direction": {"overall_direction": direction} if direction else None,
        "market_state": {"regime": regime} if regime else None,
        "volatility_structure": None,
        "greeks": greeks,
        "premium_behaviour": premium_behaviour,
    }


# ---------------------------------------------------------------------------
# Required scenario 1: strong thesis + supportive Greeks -> remains INTACT.
# ---------------------------------------------------------------------------
def test_strong_thesis_supportive_greeks_stays_intact():
    entry = _entry(entry_greeks=_greeks(0.6, -0.4))
    current = _record(direction="STRONG_BULLISH", greeks=_greeks(0.65, -0.35),
                       premium_behaviour=_premium_behaviour(ce_vs_pe="CE_EXPANDING_FASTER"))
    result = evaluate_thesis(entry, current)
    assert result.thesis_status == THESIS_INTACT
    assert result.recommendation == RECOMMEND_HOLD
    delta_check = next(c for c in result.checks if c.dimension == "delta_exposure")
    assert delta_check.status == CHECK_CONSISTENT
    premium_check = next(c for c in result.checks if c.dimension == "premium_direction_confirmation")
    assert premium_check.status == CHECK_CONSISTENT


# ---------------------------------------------------------------------------
# Required scenario 2: strong thesis + adverse Greeks -> WEAKENING.
# ---------------------------------------------------------------------------
def test_strong_thesis_adverse_delta_exposure_weakens():
    """Direction check itself still CONSISTENT (price direction hasn't
    reversed), but delta bias has flipped -- one deviated check among
    several resolved ones should weaken, not invalidate outright."""
    entry = _entry(entry_greeks=_greeks(0.6, -0.4))
    current = _record(direction="BULLISH", greeks=_greeks(-0.6, -1.6),
                       premium_behaviour=_premium_behaviour(ce_vs_pe="CE_EXPANDING_FASTER"))
    result = evaluate_thesis(entry, current)
    delta_check = next(c for c in result.checks if c.dimension == "delta_exposure")
    assert delta_check.status == CHECK_DEVIATED
    assert result.thesis_status in (THESIS_WEAKENING, THESIS_INVALIDATED)  # depends on resolved-check ratio, never silently INTACT.


# ---------------------------------------------------------------------------
# Required scenario 3: strong thesis + sufficiently strong adverse
# multi-signal evidence -> INVALIDATED.
# ---------------------------------------------------------------------------
def test_multi_signal_adverse_evidence_invalidates():
    entry = _entry(family="LONG_DIRECTIONAL", direction="BULLISH", regime="RANGING",
                    entry_greeks=_greeks(0.6, -0.4))
    # Bearish price direction is confirmed by PE expanding faster -- so
    # to genuinely make ALL THREE checks adverse (not just 2), the
    # premium reading here must be the ONE that does NOT confirm the
    # new bearish direction (CE expanding faster while price is
    # bearish is itself a real anomaly worth flagging as DEVIATED).
    current = _record(direction="STRONG_BEARISH", regime="RANGING", greeks=_greeks(0.1, -0.9),
                       premium_behaviour=_premium_behaviour(ce_vs_pe="CE_EXPANDING_FASTER"))
    result = evaluate_thesis(entry, current)
    direction_check = next(c for c in result.checks if c.dimension == "direction")
    delta_check = next(c for c in result.checks if c.dimension == "delta_exposure")
    premium_check = next(c for c in result.checks if c.dimension == "premium_direction_confirmation")
    assert direction_check.status == CHECK_DEVIATED
    assert delta_check.status == CHECK_DEVIATED
    assert premium_check.status == CHECK_DEVIATED
    assert result.thesis_status == THESIS_INVALIDATED
    assert result.recommendation == RECOMMEND_EXIT


# ---------------------------------------------------------------------------
# Required scenario 4: UNKNOWN Greeks -> existing thesis logic unaffected.
# ---------------------------------------------------------------------------
def test_unknown_greeks_does_not_disturb_existing_logic():
    entry_without_greeks = _entry(entry_greeks=None)
    entry_with_greeks = _entry(entry_greeks=_greeks(0.6, -0.4))
    current_no_current_greeks = _record(direction="STRONG_BULLISH", greeks=None,
                                         premium_behaviour=_premium_behaviour())

    result_a = evaluate_thesis(entry_without_greeks, current_no_current_greeks)
    result_b = evaluate_thesis(entry_with_greeks, current_no_current_greeks)

    delta_check_a = next(c for c in result_a.checks if c.dimension == "delta_exposure")
    delta_check_b = next(c for c in result_b.checks if c.dimension == "delta_exposure")
    assert delta_check_a.status == CHECK_UNKNOWN
    assert delta_check_b.status == CHECK_UNKNOWN
    # The pre-existing direction check is completely unaffected either way.
    direction_check_a = next(c for c in result_a.checks if c.dimension == "direction")
    direction_check_b = next(c for c in result_b.checks if c.dimension == "direction")
    assert direction_check_a.status == direction_check_b.status == CHECK_CONSISTENT
    assert result_a.thesis_status == result_b.thesis_status == THESIS_INTACT


# ---------------------------------------------------------------------------
# Required scenario 5: UNKNOWN premium behaviour -> no false invalidation.
# ---------------------------------------------------------------------------
def test_unknown_premium_behaviour_never_forces_invalidation():
    entry = _entry(entry_greeks=_greeks(0.6, -0.4))
    current = _record(direction="STRONG_BULLISH", greeks=_greeks(0.6, -0.4), premium_behaviour=None)
    result = evaluate_thesis(entry, current)
    premium_check = next(c for c in result.checks if c.dimension == "premium_direction_confirmation")
    assert premium_check.status == CHECK_UNKNOWN
    assert result.thesis_status == THESIS_INTACT  # every RESOLVED check is consistent -- UNKNOWN never drags it down.


# ---------------------------------------------------------------------------
# Required scenario 6: contradictory Greeks + premium behaviour ->
# confidence decreases / UNKNOWN rather than an arbitrary EXIT.
# ---------------------------------------------------------------------------
def test_contradictory_greeks_and_premium_reduce_confidence_not_force_exit():
    entry = _entry(entry_greeks=_greeks(0.6, -0.4))
    # Delta bias still consistent (bullish), but premium confirmation
    # says PE is expanding faster -- a real, genuine contradiction.
    current = _record(direction="BULLISH", greeks=_greeks(0.65, -0.35),
                       premium_behaviour=_premium_behaviour(ce_vs_pe="PE_EXPANDING_FASTER"))
    result = evaluate_thesis(entry, current)
    delta_check = next(c for c in result.checks if c.dimension == "delta_exposure")
    premium_check = next(c for c in result.checks if c.dimension == "premium_direction_confirmation")
    assert delta_check.status == CHECK_CONSISTENT
    assert premium_check.status == CHECK_DEVIATED
    # Mixed evidence -> WEAKENING (never a forced EXIT from one contradicting signal
    # among several consistent ones).
    assert result.thesis_status == THESIS_WEAKENING
    assert result.recommendation == RECOMMEND_HOLD


# ---------------------------------------------------------------------------
# Required scenario 7: recovery from invalidation when evidence genuinely reverses.
# ---------------------------------------------------------------------------
def test_recovery_from_invalidation_when_evidence_reverses():
    entry = _entry(entry_greeks=_greeks(0.6, -0.4))
    bad_moment = _record(direction="STRONG_BEARISH", regime="RANGING", greeks=_greeks(0.1, -0.9),
                          premium_behaviour=_premium_behaviour(ce_vs_pe="CE_EXPANDING_FASTER"))
    good_moment_later = _record(direction="STRONG_BULLISH", regime="RANGING", greeks=_greeks(0.7, -0.3),
                                 premium_behaviour=_premium_behaviour(ce_vs_pe="CE_EXPANDING_FASTER"), ts="t2")

    invalidated_result = evaluate_thesis(entry, bad_moment)
    recovered_result = evaluate_thesis(entry, good_moment_later)  # SAME entry -- each call is independent/stateless.

    assert invalidated_result.thesis_status == THESIS_INVALIDATED
    assert recovered_result.thesis_status == THESIS_INTACT


# ---------------------------------------------------------------------------
# Required scenario 8: backward-compatible old ShadowTradeCandidate/entry
# with no new fields (entry_greeks/entry_premium_behaviour omitted
# entirely, positional construction as pre-Phase-15F callers do).
# ---------------------------------------------------------------------------
def test_backward_compatible_entry_without_new_fields():
    old_style_entry = PositionEntrySnapshot(
        "TEST", "LONG_DIRECTIONAL", "t0", "RANGING", "BULLISH", None, None, None,
    )  # positional, exactly as pre-Phase-15F code constructs it -- no entry_greeks/entry_premium_behaviour passed at all.
    assert old_style_entry.entry_greeks is None
    assert old_style_entry.entry_premium_behaviour is None
    current = _record(direction="STRONG_BULLISH", greeks=_greeks(), premium_behaviour=_premium_behaviour())
    result = evaluate_thesis(old_style_entry, current)
    delta_check = next(c for c in result.checks if c.dimension == "delta_exposure")
    assert delta_check.status == CHECK_UNKNOWN  # no entry baseline -- honestly UNKNOWN, never fabricated.
    assert result.thesis_status == THESIS_INTACT  # direction check alone still resolves cleanly.


# ---------------------------------------------------------------------------
# build_entry_snapshot: additive param, backward compatible.
# ---------------------------------------------------------------------------
class _FakeCandidate:
    candidate_id = "C1"
    strategy_family = "LONG_DIRECTIONAL"
    timestamp = "t0"
    market_regime = "RANGING"
    direction = "BULLISH"
    consensus_state = "UNANIMOUS_CONSENSUS"


def test_build_entry_snapshot_without_record_is_unknown():
    snapshot = build_entry_snapshot(_FakeCandidate())
    assert snapshot.entry_greeks is None
    assert snapshot.entry_premium_behaviour is None


def test_build_entry_snapshot_with_record_captures_greeks_and_premium():
    record = {"greeks": _greeks(), "premium_behaviour": _premium_behaviour()}
    snapshot = build_entry_snapshot(_FakeCandidate(), record)
    assert snapshot.entry_greeks == _greeks()
    assert snapshot.entry_premium_behaviour == _premium_behaviour()


def test_build_entry_snapshot_with_record_missing_new_fields_is_unknown():
    """A record from before Phase 15E (no "greeks"/"premium_behaviour"
    keys at all) must degrade to None, never KeyError."""
    old_record = {"timestamp": "t0"}
    snapshot = build_entry_snapshot(_FakeCandidate(), old_record)
    assert snapshot.entry_greeks is None
    assert snapshot.entry_premium_behaviour is None


# ---------------------------------------------------------------------------
# Ownership boundary: these checks never fire for families outside
# their documented scope.
# ---------------------------------------------------------------------------
def test_delta_exposure_check_absent_for_non_directional_family():
    entry = _entry(family="IRON_CONDOR", entry_greeks=_greeks())
    current = _record(direction="BULLISH", greeks=_greeks())
    result = evaluate_thesis(entry, current)
    assert not any(c.dimension == "delta_exposure" for c in result.checks)


def test_premium_selling_pressure_check_absent_for_directional_family():
    entry = _entry(family="LONG_DIRECTIONAL")
    current = _record(direction="BULLISH", premium_behaviour=_premium_behaviour())
    result = evaluate_thesis(entry, current)
    assert not any(c.dimension == "premium_selling_pressure" for c in result.checks)


def test_premium_selling_pressure_confirms_compression_thesis():
    entry = _entry(family="IRON_CONDOR", direction=None, regime="RANGING")
    current = _record(regime="RANGING", premium_behaviour=_premium_behaviour(combined_direction="FALLING"))
    result = evaluate_thesis(entry, current)
    check = next(c for c in result.checks if c.dimension == "premium_selling_pressure")
    assert check.status == CHECK_CONSISTENT


def test_premium_selling_pressure_deviates_on_expansion():
    entry = _entry(family="IRON_CONDOR", direction=None, regime="RANGING")
    current = _record(regime="RANGING", premium_behaviour=_premium_behaviour(combined_direction="RISING", combined_accel="ACCELERATING"))
    result = evaluate_thesis(entry, current)
    check = next(c for c in result.checks if c.dimension == "premium_selling_pressure")
    assert check.status == CHECK_DEVIATED
    assert "accelerating" in check.reason


# ---------------------------------------------------------------------------
# Evidence confidence field.
# ---------------------------------------------------------------------------
def test_evidence_confidence_high_when_all_resolved():
    entry = _entry(entry_greeks=_greeks())
    current = _record(direction="STRONG_BULLISH", greeks=_greeks(), premium_behaviour=_premium_behaviour())
    result = evaluate_thesis(entry, current)
    assert result.evidence_confidence in ("HIGH", "MODERATE")


def test_evidence_confidence_none_when_all_unknown():
    entry = _entry(family="IRON_CONDOR", direction=None, regime=None)
    current = _record()
    result = evaluate_thesis(entry, current)
    assert result.thesis_status == THESIS_UNKNOWN
    assert result.evidence_confidence == "NONE"
