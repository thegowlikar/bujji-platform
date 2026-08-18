"""Phase 16C -- canonical epistemics tests.

Proves the COMPOSITION SEMANTICS, which is the part that did not exist
before. Renaming enums would have been worthless; what matters is that
uncertainty propagates correctly and explains itself.
"""
from __future__ import annotations

import pytest

from bujji.epistemics import adapters
from bujji.epistemics.lineage import (
    AUTHORITATIVE, DERIVED, EPHEMERAL, Lineage, calc_version_for, look_ahead_violation,
)
from bujji.epistemics.uncertainty import (
    DEGRADED, GAP, HIGH, INSUFFICIENT_HISTORY, KNOWN, LOW, MODERATE, NONE,
    NOT_APPLICABLE, NOT_AVAILABLE, STALE, UNKNOWN, Input, Uncertainty,
    cap, compose, demote, insufficient, rank, stale_if_older_than, unknown, weaker,
)


def known(conf=HIGH, name="x"):
    return Uncertainty(state=KNOWN, confidence=conf, provenance=(name,))


# --- band algebra -------------------------------------------------------
def test_rank_ordering():
    assert rank(NONE) < rank(LOW) < rank(MODERATE) < rank(HIGH)


def test_weaker_and_demote_and_cap():
    assert weaker(HIGH, LOW) == LOW
    assert demote(HIGH) == MODERATE
    assert demote(NONE) == NONE          # floors, never wraps
    assert cap(HIGH, LOW) == LOW
    assert cap(LOW, HIGH) == LOW         # cap never promotes


def test_invalid_values_rejected():
    with pytest.raises(ValueError):
        Uncertainty(state="MADE_UP")
    with pytest.raises(ValueError):
        Uncertainty(confidence="VERY_HIGH")


# --- THE FIVE REQUIRED ANSWERS -----------------------------------------
def test_answer1_known_but_stale_retains_value_but_is_not_actionable():
    """Feature KNOWN but stale -> value retained, confidence capped LOW,
    limiting_factor names freshness. Analysis may use it; risk must not."""
    u = stale_if_older_than(known(HIGH), age_s=120.0, bound_s=30.0)
    assert u.state == STALE
    assert u.confidence == LOW
    assert "freshness" in u.limiting_factor
    assert u.carries_value is True          # still usable for analysis
    assert u.is_actionable is False         # but NOT for committing capital


def test_answer2_critical_unknown_absorbs():
    out = compose([Input("spot", known(HIGH), critical=True),
                   Input("iv", unknown("no quote"), critical=True)])
    assert out.state == UNKNOWN
    assert out.confidence == NONE
    assert out.limiting_factor == "iv:UNKNOWN"


def test_answer3_non_critical_unknown_degrades_but_does_not_block():
    out = compose([Input("spot", known(HIGH), critical=True),
                   Input("oi", unknown("no oi"), critical=False)])
    assert out.state == DEGRADED
    assert out.confidence == MODERATE       # one band down, not NONE
    assert "oi" in out.limiting_factor and "non-critical" in out.limiting_factor
    assert out.completeness == 0.5


def test_answer4_gap_taints_and_breaks_range_dependent_features():
    gapped = Uncertainty(state=GAP, confidence=HIGH)
    # ordinary feature: tainted, capped LOW, still carries a value
    ordinary = compose([Input("candles", gapped, critical=True)])
    assert ordinary.state == GAP
    assert ordinary.confidence == LOW
    assert ordinary.carries_value is True

    # range-dependent (ATR / Bollinger / realised vol): a gap breaks the
    # DEFINITION, so no value at all -- not merely a weak one.
    ranged = compose([Input("candles", gapped, critical=True)], range_dependent=True)
    assert ranged.state == UNKNOWN
    assert ranged.carries_value is False
    assert "range-dependent" in ranged.limiting_factor


def test_answer5_higher_timeframe_not_yet_formed_is_insufficient_not_unknown():
    """Distinct because it SELF-HEALS with time, and must never be
    learned from as though the answer were unknowable."""
    u = insufficient("daily bar needs 20 obs, has 6")
    assert u.state == INSUFFICIENT_HISTORY
    assert u.state != UNKNOWN
    out = compose([Input("daily_ema", u, critical=True)])
    assert out.state == INSUFFICIENT_HISTORY   # absorbed AS ITSELF, not flattened to UNKNOWN


# --- core composition rules --------------------------------------------
def test_min_rule_never_exceeds_weakest_critical_input():
    out = compose([Input("a", known(HIGH), critical=True),
                   Input("b", known(LOW), critical=True)])
    assert out.confidence == LOW
    assert out.limiting_factor == "b:confidence=LOW"


def test_derived_confidence_can_never_be_stronger_than_inputs():
    for c in (NONE, LOW, MODERATE, HIGH):
        out = compose([Input("only", known(c), critical=True)])
        assert rank(out.confidence) <= rank(c)


def test_not_applicable_is_ignored_and_does_not_degrade_siblings():
    """'No management event ever happened' is not missing evidence."""
    out = compose([Input("a", known(HIGH), critical=True),
                   Input("mgmt", Uncertainty(state=NOT_APPLICABLE, confidence=NONE), critical=True)])
    assert out.state == KNOWN
    assert out.confidence == HIGH
    assert out.limiting_factor is None


def test_not_available_absorbs_like_unknown_but_keeps_its_own_state():
    out = compose([Input("legacy", Uncertainty(state=NOT_AVAILABLE, confidence=NONE), critical=True)])
    assert out.state == NOT_AVAILABLE       # preserved, not flattened
    assert out.carries_value is False


def test_limiting_factor_present_whenever_confidence_reduced():
    out = compose([Input("a", known(HIGH), critical=True),
                   Input("b", known(MODERATE), critical=True)])
    assert out.confidence == MODERATE
    assert out.limiting_factor is not None   # MUST explain itself


def test_no_limiting_factor_when_nothing_was_limited():
    out = compose([Input("a", known(HIGH), critical=True),
                   Input("b", known(HIGH), critical=True)])
    assert out.confidence == HIGH
    assert out.limiting_factor is None


def test_provenance_accumulates_across_inputs():
    out = compose([Input("a", known(HIGH, "src_a"), critical=True),
                   Input("b", known(HIGH, "src_b"), critical=True)])
    assert set(out.provenance) == {"src_a", "src_b"}


def test_is_actionable_gate():
    assert known(HIGH).is_actionable is True
    assert known(MODERATE).is_actionable is True
    assert known(LOW).is_actionable is False          # too weak to commit capital
    assert Uncertainty(state=GAP, confidence=HIGH).is_actionable is False
    assert Uncertainty(state=STALE, confidence=HIGH).is_actionable is False


def test_roundtrip_serialisation():
    u = compose([Input("a", known(LOW), critical=True)])
    assert Uncertainty.from_dict(u.to_dict()).to_dict() == u.to_dict()


# --- full-stack propagation --------------------------------------------
def test_uncertainty_propagates_observation_to_decision():
    """Observation -> Feature -> Phenomenon -> State -> Decision.
    A gap at the observation layer must still be visible, and still
    named, at the decision layer."""
    observation = Uncertainty(state=GAP, confidence=HIGH, provenance=("tick_store",))
    feature = compose([Input("candles", observation, critical=True)])
    phenomenon = compose([Input("atr", feature, critical=True)])
    state = compose([Input("volatility", phenomenon, critical=True)])
    decision = compose([Input("state", state, critical=True)])

    assert decision.state == GAP                 # not laundered away
    assert decision.confidence == LOW
    assert decision.is_actionable is False       # risk layer would refuse
    assert "tick_store" in decision.provenance   # traceable to the root


# --- adapters: no package rewritten -------------------------------------
def test_adapter_msi_confidence():
    assert adapters.from_msi_confidence("HIGH").confidence == HIGH
    assert adapters.from_msi_confidence("MEDIUM").confidence == MODERATE   # the stray one
    assert adapters.from_msi_confidence("NONE").state == UNKNOWN
    assert adapters.from_msi_confidence(None).state == UNKNOWN


def test_adapter_pnl_partial_is_degraded_not_known():
    assert adapters.from_pnl_status("COMPLETE").state == KNOWN
    assert adapters.from_pnl_status("PARTIAL").state == DEGRADED
    assert adapters.from_pnl_status("UNKNOWN").state == UNKNOWN


def test_adapter_portfolio_status():
    assert adapters.from_portfolio_status("KNOWN").state == KNOWN
    assert adapters.from_portfolio_status("PARTIAL").state == DEGRADED
    assert adapters.from_portfolio_status("UNKNOWN").state == UNKNOWN


def test_adapter_memory_epistemic_is_one_to_one():
    """15N already had the right idea -- it maps exactly."""
    for v, expect in (("KNOWN", KNOWN), ("UNKNOWN", UNKNOWN),
                      ("NOT_APPLICABLE", NOT_APPLICABLE), ("NOT_AVAILABLE", NOT_AVAILABLE)):
        assert adapters.from_memory_epistemic(v).state == expect


def test_adapter_not_applicable_does_not_degrade_a_composition():
    na = adapters.from_memory_epistemic("NOT_APPLICABLE", source="mgmt")
    out = compose([Input("a", known(HIGH), critical=True), Input("mgmt", na, critical=True)])
    assert out.confidence == HIGH


def test_every_adapter_returns_canonical_type():
    for name, fn in adapters.ADAPTERS.items():
        assert isinstance(fn("HIGH", source=name), Uncertainty), name


# --- lineage ------------------------------------------------------------
def test_calc_version_is_content_derived_and_stable():
    a = calc_version_for("def ema(...)", {"period": 20})
    assert a == calc_version_for("def ema(...)", {"period": 20})
    assert a != calc_version_for("def ema(...)", {"period": 50})     # params matter
    assert a != calc_version_for("def ema_v2(...)", {"period": 20})  # source matters


def test_derived_requires_calc_version():
    with pytest.raises(ValueError):
        Lineage(data_class=DERIVED, as_of="2026-08-11T10:00:00", calc_version="")


def test_authoritative_is_reproducible_by_definition():
    lin = Lineage(data_class=AUTHORITATIVE, as_of="2026-08-11T10:00:00", calc_version="")
    assert lin.is_reproducible is True
    assert lin.missing_fields() == ()


def test_derived_missing_versions_are_named_not_hidden():
    lin = Lineage(data_class=DERIVED, as_of="t", calc_version="CV-1")
    assert lin.is_reproducible is False
    assert set(lin.missing_fields()) == {"code_version", "config_version", "source_event_ids"}


def test_fully_specified_derived_is_reproducible():
    lin = Lineage(data_class=DERIVED, as_of="t", calc_version="CV-1",
                  code_version="sha", config_version="cfg1", source_event_ids=("e1",))
    assert lin.is_reproducible is True
    assert lin.missing_fields() == ()


def test_descends_from_accumulates_source_events_without_duplicates():
    p1 = Lineage(data_class=DERIVED, as_of="t", calc_version="A", source_event_ids=("e1", "e2"))
    p2 = Lineage(data_class=DERIVED, as_of="t", calc_version="B", source_event_ids=("e2", "e3"))
    child = Lineage(data_class=DERIVED, as_of="t", calc_version="C").descends_from(p1, p2)
    assert child.source_event_ids == ("e1", "e2", "e3")


def test_look_ahead_violation_detected():
    lin = Lineage(data_class=DERIVED, as_of="2026-08-11T10:00:00", calc_version="CV-1")
    assert look_ahead_violation(lin, "2026-08-11T10:00:01") is True    # future source
    assert look_ahead_violation(lin, "2026-08-11T09:59:59") is False


def test_lineage_roundtrip():
    lin = Lineage(data_class=DERIVED, as_of="t", calc_version="CV-1",
                  code_version="sha", source_event_ids=("e1",))
    assert Lineage.from_dict(lin.to_dict()).to_dict() == lin.to_dict()


def test_data_class_validated():
    with pytest.raises(ValueError):
        Lineage(data_class="SOMETHING", as_of="t", calc_version="CV")


def test_ephemeral_is_a_first_class_class():
    """A forming candle is EPHEMERAL: never persisted, always
    reconstructable. It must not be mistaken for DERIVED history."""
    lin = Lineage(data_class=EPHEMERAL, as_of="t", calc_version="CV-1")
    assert lin.data_class == EPHEMERAL
