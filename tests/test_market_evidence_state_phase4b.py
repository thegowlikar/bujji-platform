"""Tests -- MarketEvidenceState, Shadow Campaign v2 Phase 4B."""
from __future__ import annotations

from bujji.market_state.evidence_boundary import build_market_evidence_state
from bujji.market_state.models import MarketDirectionSummary, MarketState

TS = "2026-08-03T09:15:00+05:30"


def make_market_state(**overrides):
    defaults = dict(
        timestamp=TS, regime="TRENDING", volatility_state="NORMAL", liquidity_state="TIGHT",
        price_structure="RANGE_BOUND", market_structure="MID_RANGE", participant_positioning="BULLISH_POSITIONING",
        active_events=("PRICE_CHANGED", "OI_CHANGED"), active_episodes=("EP-1",), overall_confidence="HIGH",
        uncertainties=(), evidence_ids=("OBS-1", "OBS-2"),
        market_direction=MarketDirectionSummary(
            direction="STRONG_BULLISH", confidence="HIGH", evidence=("PSI-1", "MSSI-1"), uncertainties=(),
        ),
    )
    defaults.update(overrides)
    return MarketState(**defaults)


# --- 1. Complete MarketState ---

def test_complete_market_state_all_fields_mapped_correctly():
    ms = make_market_state()
    mes = build_market_evidence_state(ms)
    assert mes.timestamp == TS
    assert mes.source_market_state_timestamp == TS
    assert mes.regime == "TRENDING"
    assert mes.direction == "STRONG_BULLISH"
    assert mes.volatility_state == "NORMAL"
    assert mes.liquidity_state == "TIGHT"
    assert mes.participant_positioning == "BULLISH_POSITIONING"
    assert mes.price_structure == "RANGE_BOUND"
    assert mes.market_structure == "MID_RANGE"
    assert mes.active_episode_ids == ("EP-1",)
    assert mes.active_event_types == ("PRICE_CHANGED", "OI_CHANGED")
    assert mes.overall_confidence == "HIGH"
    assert mes.direction_confidence == "HIGH"
    assert mes.missing_evidence == ()
    assert set(mes.evidence_ids) == {"OBS-1", "OBS-2", "PSI-1", "MSSI-1"}


# --- 2. Missing optional fields ---

def test_missing_volatility_reported_in_missing_evidence_no_crash():
    ms = make_market_state(volatility_state=None)
    mes = build_market_evidence_state(ms)
    assert mes.volatility_state is None
    assert "volatility_state" in mes.missing_evidence
    assert "regime" not in mes.missing_evidence


def test_missing_participant_positioning_reported_no_crash():
    ms = make_market_state(participant_positioning=None)
    mes = build_market_evidence_state(ms)
    assert mes.participant_positioning is None
    assert "participant_positioning" in mes.missing_evidence


def test_missing_direction_reported_no_crash():
    ms = make_market_state(market_direction=None)
    mes = build_market_evidence_state(ms)
    assert mes.direction is None
    assert mes.direction_confidence is None
    assert "direction" in mes.missing_evidence
    # Direction's own evidence/uncertainties honestly empty, never fabricated.
    assert mes.evidence_ids == ms.evidence_ids or set(mes.evidence_ids) == set(ms.evidence_ids)


def test_missing_evidence_reports_only_actual_missing_fields():
    ms = make_market_state(volatility_state=None, participant_positioning=None)
    mes = build_market_evidence_state(ms)
    assert set(mes.missing_evidence) == {"volatility_state", "participant_positioning"}
    assert len(mes.missing_evidence) == 2


# --- 3. Direction propagation ---

def test_bullish_direction_preserved():
    ms = make_market_state(market_direction=MarketDirectionSummary(
        direction="STRONG_BULLISH", confidence="HIGH", evidence=(), uncertainties=(),
    ))
    mes = build_market_evidence_state(ms)
    assert mes.direction == "STRONG_BULLISH"


def test_conflicting_direction_preserved_as_mixed():
    ms = make_market_state(market_direction=MarketDirectionSummary(
        direction="MIXED", confidence="LOW", evidence=(), uncertainties=("conflict among lenses",),
    ))
    mes = build_market_evidence_state(ms)
    assert mes.direction == "MIXED"
    assert mes.direction_confidence == "LOW"
    assert "conflict among lenses" in mes.uncertainties


def test_unknown_direction_preserved():
    ms = make_market_state(market_direction=MarketDirectionSummary(
        direction="UNKNOWN", confidence="NONE", evidence=(), uncertainties=(),
    ))
    mes = build_market_evidence_state(ms)
    assert mes.direction == "UNKNOWN"
    assert mes.direction_confidence == "NONE"


# --- 4. Uncertainty preservation ---

def test_market_state_uncertainty_retained():
    ms = make_market_state(uncertainties=("liquidity data insufficient",))
    mes = build_market_evidence_state(ms)
    assert "liquidity data insufficient" in mes.uncertainties


def test_direction_uncertainty_retained_alongside_market_state_uncertainty():
    ms = make_market_state(
        uncertainties=("volatility unavailable",),
        market_direction=MarketDirectionSummary(
            direction="MIXED", confidence="LOW", evidence=(), uncertainties=("market direction conflict",),
        ),
    )
    mes = build_market_evidence_state(ms)
    assert "volatility unavailable" in mes.uncertainties
    assert "market direction conflict" in mes.uncertainties
    assert len(mes.uncertainties) == 2  # concatenated, not deduped, not dropped


# --- 5. Provenance preservation ---

def test_evidence_ids_preserved_and_combined():
    ms = make_market_state(
        evidence_ids=("OBS-1",),
        market_direction=MarketDirectionSummary(direction="UP", confidence="HIGH", evidence=("PSI-1",), uncertainties=()),
    )
    mes = build_market_evidence_state(ms)
    assert "OBS-1" in mes.evidence_ids
    assert "PSI-1" in mes.evidence_ids


def test_timestamp_preserved():
    ms = make_market_state(timestamp="2026-08-03T10:00:00+05:30")
    mes = build_market_evidence_state(ms)
    assert mes.timestamp == "2026-08-03T10:00:00+05:30"
    assert mes.source_market_state_timestamp == "2026-08-03T10:00:00+05:30"
