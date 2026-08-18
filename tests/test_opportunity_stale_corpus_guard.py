"""Phase 15P -- stale-corpus guard.

FINDING (Phase 15P): the archived `shadow_sessions/` artifacts were
recorded by a build that PREDATES Phase 14B-P1's `STATE_LEAN_MAP`
additions (the real MSI vocabulary: BALANCE, CORRECTING, TRENDING,
NEAR_RESISTANCE, INSIDE_RANGE, NEUTRAL_POSITIONING, STABLE,
COMPRESSED, ...). Under that older build every one of those states fell
through to LEAN_AMBIGUOUS, so `considered` was empty and every cycle
resolved to MONITOR with confidence NONE.

Consequence: the recorded `opportunity.opportunity_state` field in
those files reflects OLD CODE, not current behaviour. Phases 14B
through 15O each concluded "MONITOR dominance / no candidate possible"
from this corpus; that conclusion does not hold for the current build.

This test exists so the mistake cannot silently recur: it asserts the
divergence is real and understood. It is skipped when the archived
corpus is not present (e.g. a clean checkout)."""
from __future__ import annotations

import glob
import json
import os

import pytest

from bujji.msi_decision_synthesis import taxonomy
from bujji.msi_decision_synthesis.engine import synthesize
from bujji.msi_decision_synthesis.models import DomainSignal

_CONF = {"HIGH": 0.9, "MODERATE": 0.6, "LOW": 0.3, "NONE": 0.0, "UNKNOWN": 0.0}
_STRENGTH = {"STRONG": 0.9, "MODERATE": 0.6, "WEAK": 0.3, "NONE": 0.0, "UNKNOWN": 0.0}
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _get(rec, *path):
    cur = rec
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def _signals_from(rec):
    return (
        DomainSignal(taxonomy.DOMAIN_PRICE_STRUCTURE, _get(rec, "price_structure", "structure_state"),
                     _CONF.get(_get(rec, "price_structure", "confidence"), 0.0), ()),
        DomainSignal(taxonomy.DOMAIN_SUPPORT_RESISTANCE, _get(rec, "market_structure", "structure_location"),
                     _CONF.get(_get(rec, "market_structure", "confidence"), 0.0), ()),
        DomainSignal("MARKET_DIRECTION", _get(rec, "market_direction", "overall_direction"),
                     _CONF.get(_get(rec, "market_direction", "overall_confidence"), 0.0), ()),
        DomainSignal(taxonomy.DOMAIN_OPTIONS_MARKET_STRUCTURE, _get(rec, "participant_positioning", "positioning_bias"),
                     _STRENGTH.get(_get(rec, "participant_positioning", "positioning_strength"), 0.0), ()),
        DomainSignal(taxonomy.DOMAIN_VOLATILITY_STRUCTURE, _get(rec, "volatility_structure", "volatility_regime"),
                     _CONF.get(_get(rec, "volatility_structure", "confidence"), 0.0), ()),
    )


def _archived_cycles():
    pattern = os.path.join(_REPO_ROOT, "shadow_sessions", "*", "intelligence_cycle.jsonl")
    for path in sorted(glob.glob(pattern)):
        for line in open(path):
            if line.strip():
                yield json.loads(line)


def test_archived_opportunity_states_are_stale_relative_to_current_code():
    """Proves the archived corpus cannot be used to judge current
    Opportunity behaviour: replaying its OWN persisted domain states
    through the CURRENT engine yields actionable states that the
    recorded field does not contain."""
    cycles = list(_archived_cycles())
    if not cycles:
        pytest.skip("archived shadow_sessions corpus not present in this checkout")

    recorded_actionable = sum(
        1 for c in cycles
        if _get(c, "opportunity", "opportunity_state") in taxonomy.OPPORTUNITY_TYPES
    )
    current_actionable = sum(
        1 for c in cycles
        if synthesize(_signals_from(c), None, (), timestamp="t").opportunity_state in taxonomy.OPPORTUNITY_TYPES
    )

    assert recorded_actionable == 0, (
        "archived corpus unexpectedly contains actionable opportunity states -- "
        "the stale-corpus finding may no longer hold; re-verify Phase 15P's conclusion"
    )
    assert current_actionable > 0, (
        "current engine produces NO actionable state from the archived domain states -- "
        "this contradicts Phase 15P's forensic finding and must be re-investigated before "
        "concluding anything about MONITOR dominance"
    )


def test_current_engine_still_returns_monitor_for_genuinely_ambiguous_archived_cycles():
    """The complement: the archived cycles whose domain states really
    were all-ambiguous must STILL resolve to a non-opportunity state
    under current code -- proving the current engine did not simply
    become permissive."""
    cycles = list(_archived_cycles())
    if not cycles:
        pytest.skip("archived shadow_sessions corpus not present in this checkout")

    non_opportunity = sum(
        1 for c in cycles
        if synthesize(_signals_from(c), None, (), timestamp="t").opportunity_state in taxonomy.NON_OPPORTUNITY_STATES
    )
    assert non_opportunity > 0, (
        "current engine finds an opportunity in EVERY archived cycle -- that would indicate a "
        "permissiveness defect, not a fixed vocabulary"
    )
