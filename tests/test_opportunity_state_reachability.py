"""Phase 15P Step 4/8 -- Opportunity State reachability + anti-fabrication.

Purpose is NOT to make the system trade. It is to prove the documented
ontology is actually implementable: every state the taxonomy declares
must be reachable from some real domain-signal combination, AND the
non-opportunity states must remain reachable so the system can always
honestly say "no opportunity".
"""
from __future__ import annotations

import pytest

from bujji.msi_decision_synthesis import taxonomy
from bujji.msi_decision_synthesis.engine import synthesize
from bujji.msi_decision_synthesis.models import DomainSignal

PS = taxonomy.DOMAIN_PRICE_STRUCTURE
SR = taxonomy.DOMAIN_SUPPORT_RESISTANCE
MD = "MARKET_DIRECTION"
OMS = taxonomy.DOMAIN_OPTIONS_MARKET_STRUCTURE
VS = taxonomy.DOMAIN_VOLATILITY_STRUCTURE


def sig(domain, state, confidence=0.9):
    return DomainSignal(domain_name=domain, state=state, confidence=confidence, evidence_ids=())


def resolve(*signals):
    return synthesize(tuple(signals), None, (), timestamp="t").opportunity_state


# --- Every documented OPPORTUNITY type must be reachable ------------------
def test_directional_opportunity_is_reachable():
    assert resolve(sig(MD, "STRONG_BULLISH"), sig(PS, "TRENDING")) == taxonomy.OPPORTUNITY_STATE_DIRECTIONAL


def test_breakout_opportunity_is_reachable():
    assert resolve(sig(SR, "ABOVE_RESISTANCE"), sig(PS, "TRANSITIONING")) == taxonomy.OPPORTUNITY_STATE_BREAKOUT


def test_volatility_opportunity_is_reachable():
    assert resolve(sig(VS, "COMPRESSED"), sig(PS, "TRANSITIONING")) == taxonomy.OPPORTUNITY_STATE_VOLATILITY


def test_neutral_opportunity_is_reachable():
    assert resolve(sig(OMS, "NEUTRAL_POSITIONING"), sig(VS, "STABLE")) == taxonomy.OPPORTUNITY_STATE_NEUTRAL


def test_mean_reversion_opportunity_is_reachable():
    assert resolve(sig(SR, "INSIDE_RANGE"), sig(PS, "CORRECTING")) == taxonomy.OPPORTUNITY_STATE_MEAN_REVERSION


def test_every_documented_opportunity_type_is_reachable():
    """Aggregate proof -- no declared opportunity type is dead ontology."""
    reached = {
        resolve(sig(MD, "STRONG_BULLISH"), sig(PS, "TRENDING")),
        resolve(sig(SR, "ABOVE_RESISTANCE"), sig(PS, "TRANSITIONING")),
        resolve(sig(VS, "COMPRESSED"), sig(PS, "TRANSITIONING")),
        resolve(sig(OMS, "NEUTRAL_POSITIONING"), sig(VS, "STABLE")),
        resolve(sig(SR, "INSIDE_RANGE"), sig(PS, "CORRECTING")),
    }
    assert set(taxonomy.OPPORTUNITY_TYPES) <= reached, f"unreachable types: {set(taxonomy.OPPORTUNITY_TYPES) - reached}"


# --- Non-opportunity states must remain reachable (anti-fabrication) ------
def test_wait_is_reachable_when_no_signals_exist():
    assert resolve() == taxonomy.OPPORTUNITY_STATE_WAIT


def test_monitor_is_reachable_when_every_signal_is_ambiguous():
    assert resolve(sig(PS, "UNKNOWN"), sig(MD, "UNKNOWN"), sig(VS, "UNKNOWN")) == taxonomy.OPPORTUNITY_STATE_MONITOR


def test_monitor_reachable_with_transitioning_and_mixed_only():
    """Real observed combination: TRANSITIONING price structure +
    MIXED positioning + UNKNOWN elsewhere -- genuinely ambiguous."""
    state = resolve(sig(PS, "TRANSITIONING"), sig(OMS, "MIXED_POSITIONING"),
                     sig(MD, "UNKNOWN"), sig(SR, "UNKNOWN"), sig(VS, "UNKNOWN"))
    assert state == taxonomy.OPPORTUNITY_STATE_MONITOR


# --- UNKNOWN propagation --------------------------------------------------
def test_unknown_never_counts_as_agreement():
    """An UNKNOWN domain must never become supporting evidence."""
    result = synthesize((sig(MD, "UNKNOWN"), sig(PS, "UNKNOWN")), None, (), timestamp="t")
    assert result.supporting_domains == ()
    assert result.confidence_level == taxonomy.CONFIDENCE_NONE


def test_adding_unknown_domains_never_changes_the_state():
    """Adding purely-UNKNOWN evidence must not alter a resolved state --
    UNKNOWN is information, never a vote."""
    base = resolve(sig(MD, "STRONG_BULLISH"), sig(PS, "TRENDING"))
    with_unknowns = resolve(sig(MD, "STRONG_BULLISH"), sig(PS, "TRENDING"),
                             sig(SR, "UNKNOWN"), sig(VS, "UNKNOWN"), sig(OMS, "UNKNOWN_POSITIONING"))
    assert base == with_unknowns


def test_liquidity_precondition_domain_never_votes():
    """PRECONDITION_DOMAINS are always AMBIGUOUS regardless of state --
    they gate, they never vote."""
    for domain in taxonomy.PRECONDITION_DOMAINS:
        result = synthesize((sig(domain, "Strong"),), None, (), timestamp="t")
        assert result.supporting_domains == ()
        assert result.opportunity_state == taxonomy.OPPORTUNITY_STATE_MONITOR


# --- Conflict / tie behaviour --------------------------------------------
def test_tie_between_leans_resolves_conservatively():
    """1 opportunity-forming vs 1 neutral-forming -- the conservative
    NEUTRAL lean must win, never the opportunity one."""
    state = resolve(sig(MD, "STRONG_BULLISH"), sig(VS, "STABLE"))
    assert state == taxonomy.OPPORTUNITY_STATE_NEUTRAL


def test_conflicting_signals_reduce_confidence_not_state_honesty():
    strong = synthesize((sig(MD, "STRONG_BULLISH"), sig(PS, "TRENDING")), None, (), timestamp="t")
    conflicted = synthesize((sig(MD, "STRONG_BULLISH"), sig(PS, "TRENDING"), sig(VS, "STABLE"),
                              sig(OMS, "NEUTRAL_POSITIONING")), None, (), timestamp="t")
    order = {taxonomy.CONFIDENCE_NONE: 0, taxonomy.CONFIDENCE_LOW: 1,
             taxonomy.CONFIDENCE_MODERATE: 2, taxonomy.CONFIDENCE_HIGH: 3}
    assert order[conflicted.confidence_level] <= order[strong.confidence_level]


# --- Determinism ----------------------------------------------------------
def test_synthesis_is_deterministic():
    signals = (sig(MD, "STRONG_BULLISH"), sig(PS, "TRENDING"), sig(VS, "COMPRESSED"))
    a = synthesize(signals, None, (), timestamp="t")
    b = synthesize(signals, None, (), timestamp="t")
    assert a.opportunity_state == b.opportunity_state
    assert a.assessment_id == b.assessment_id


# --- ANTI-FABRICATION (Step 8) -------------------------------------------
def test_opportunity_cannot_be_made_actionable_by_downstream_demand():
    """THE critical anti-fabrication proof. `synthesize` takes ONLY
    domain signals -- it has no parameter through which a downstream
    component (strategy selection, trade intent, a runtime that 'needs'
    a trade) could request or bias an actionable state. Verified by
    inspecting the real signature."""
    import inspect
    params = set(inspect.signature(synthesize).parameters)
    forbidden = {"desired_state", "force", "require_opportunity", "minimum_state",
                 "allow_monitor", "bias", "target_state", "needs_trade"}
    assert not (params & forbidden), f"synthesize() exposes a state-forcing parameter: {params & forbidden}"


def test_no_evidence_can_never_produce_an_actionable_state():
    """With zero or purely-UNKNOWN evidence the engine must NEVER
    return an opportunity type -- no amount of downstream need can
    manufacture one."""
    assert resolve() in taxonomy.NON_OPPORTUNITY_STATES
    assert resolve(sig(PS, "UNKNOWN")) in taxonomy.NON_OPPORTUNITY_STATES
    assert resolve(sig(PS, "UNKNOWN"), sig(MD, "UNKNOWN"), sig(SR, "UNKNOWN"),
                    sig(VS, "UNKNOWN"), sig(OMS, "UNKNOWN_POSITIONING")) in taxonomy.NON_OPPORTUNITY_STATES


def test_monitor_remains_a_first_class_reachable_outcome():
    """The system must always retain the ability to say
    'MONITOR -> no opportunity -> no trade'. If this test ever fails,
    a change has made honest inaction impossible."""
    assert taxonomy.OPPORTUNITY_STATE_MONITOR in taxonomy.NON_OPPORTUNITY_STATES
    assert resolve(sig(PS, "UNKNOWN"), sig(MD, "UNKNOWN")) == taxonomy.OPPORTUNITY_STATE_MONITOR


def test_non_opportunity_states_map_to_no_compatible_families():
    """Structural anti-fabrication: even if a non-opportunity state
    somehow reached eligibility, it offers zero compatible families."""
    from bujji.msi_decision_synthesis.engine import compatible_strategy_families
    for state in taxonomy.NON_OPPORTUNITY_STATES:
        compatible, _ = compatible_strategy_families(state)
        assert compatible == (), f"{state} exposes compatible families {compatible}"
