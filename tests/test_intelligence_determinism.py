"""Intelligence Determinism Hardening -- Phase 19.2.2.

Proves the five properties the phase's own spec requires before
MarketIntelligenceSnapshot can be built on top of these brains:

1. Historical replay of the same timestamp produces identical intelligence
   output (RegimeBrain used as the representative case -- its `analyze()`
   is a pure function of candles + context, no I/O).
2. Live and historical mode use IDENTICAL brain logic -- same output for
   the same inputs regardless of `execution_mode`, since no brain branches
   on it (this is a design guarantee, not a per-brain special case).
3. No brain calls the system clock internally -- a structural, source-
   inspection test (same pattern as this codebase's existing
   `test_never_requests_greeks`-style tests), not a runtime behavior test,
   since a runtime test could pass by accident if a stale cached value
   happened to match.
4. Evidence can trace back to source observations -- `evidence_lineage`
   entries carry the same `reality_snapshot_reference` and
   `observation_references` the caller supplied via `IntelligenceContext`.
5. ATM volatility-reference selection (`VolatilityReferencePolicy.ATM_STRADDLE`)
   is deterministic -- same chain in, same strike out, every time, and
   matches the exact `option_chain_adapter.py` selection expression this
   policy was extracted from (not reinvented).
"""
from __future__ import annotations

import ast
import glob
import os
from datetime import datetime

import pytest

from bujji.core.clock import IST
from bujji.core.models import Candle
from bujji.intelligence.context import (
    EXECUTION_MODE_HISTORICAL_REPLAY,
    EXECUTION_MODE_LIVE,
    IntelligenceContext,
)
from bujji.intelligence.evidence import IntelligenceEvidence, wrap_evidence
from bujji.intelligence.regime_brain import RegimeBrain
from bujji.intelligence.volatility_policy import (
    VOLATILITY_REFERENCE_ATM_STRADDLE,
    select_atm_strike,
    select_atm_straddle,
)

INTELLIGENCE_DIR = os.path.join(os.path.dirname(__file__), "..", "bujji", "intelligence")
IN_SCOPE_BRAIN_FILES = [
    "regime_brain.py", "structure_brain.py", "liquidity_brain.py",
    "volatility_brain.py", "greeks_brain.py", "event_brain.py",
]


def _candles(closes: list[float]) -> list[Candle]:
    from datetime import timedelta
    out = []
    for i, c in enumerate(closes):
        ts = datetime(2026, 7, 20, 9, 20, tzinfo=IST) + timedelta(minutes=5 * i)
        out.append(Candle(ts, c, c + 1, c - 1, c, 1000))
    return out


# ---------------------------------------------------------------------- #
# Property 1: historical replay determinism
# ---------------------------------------------------------------------- #
def test_replay_of_same_timestamp_produces_identical_output():
    closes = [24000 + i * 3 for i in range(10)]
    candles = _candles(closes)
    fixed_time = datetime(2026, 7, 20, 9, 45, tzinfo=IST)

    context_a = IntelligenceContext(as_of_time=fixed_time, execution_mode=EXECUTION_MODE_HISTORICAL_REPLAY)
    context_b = IntelligenceContext(as_of_time=fixed_time, execution_mode=EXECUTION_MODE_HISTORICAL_REPLAY)

    brain = RegimeBrain()
    reading_a = brain.analyze(candles, context_a)
    reading_b = brain.analyze(candles, context_b)

    assert reading_a.as_of == reading_b.as_of == fixed_time
    assert reading_a.regime == reading_b.regime
    assert reading_a.confidence == reading_b.confidence
    assert reading_a.evidence == reading_b.evidence


# ---------------------------------------------------------------------- #
# Property 2: live and historical mode use identical brain logic
# ---------------------------------------------------------------------- #
def test_live_and_historical_execution_modes_produce_identical_reading():
    closes = [24000 + i * 3 for i in range(10)]
    candles = _candles(closes)
    fixed_time = datetime(2026, 7, 20, 9, 45, tzinfo=IST)

    live_context = IntelligenceContext(as_of_time=fixed_time, execution_mode=EXECUTION_MODE_LIVE)
    replay_context = IntelligenceContext(as_of_time=fixed_time, execution_mode=EXECUTION_MODE_HISTORICAL_REPLAY)

    brain = RegimeBrain()
    live_reading = brain.analyze(candles, live_context)
    replay_reading = brain.analyze(candles, replay_context)

    # Only the context's own execution_mode differs -- the reading itself
    # carries no execution_mode field, so if the brain is genuinely mode-
    # agnostic (the whole point of this phase), every other field matches.
    assert live_reading.regime == replay_reading.regime
    assert live_reading.confidence == replay_reading.confidence
    assert live_reading.evidence == replay_reading.evidence
    assert live_reading.as_of == replay_reading.as_of


# ---------------------------------------------------------------------- #
# Property 3: no brain calls the system clock internally (structural)
# ---------------------------------------------------------------------- #
def test_no_in_scope_brain_imports_or_calls_now_ist():
    """Source-inspection test, not a runtime test -- a runtime test could
    pass by accident if a stale cached value happened to match the real
    clock. This directly greps for the exact bug Phase 19.2.1 confirmed
    six-for-six across these files."""
    for filename in IN_SCOPE_BRAIN_FILES:
        path = os.path.join(INTELLIGENCE_DIR, filename)
        with open(path) as f:
            source = f.read()
        assert "now_ist" not in source, (
            f"{filename} still references now_ist() -- clock injection incomplete"
        )
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and "clock" in node.module:
                pytest.fail(f"{filename} still imports from a clock module: {node.module}")


# ---------------------------------------------------------------------- #
# Property 4: evidence traces back to source observations
# ---------------------------------------------------------------------- #
def test_evidence_lineage_carries_context_provenance_verbatim():
    raw_evidence = {"efficiency_ratio": 0.62, "net_move": 45.0}
    context = IntelligenceContext(
        as_of_time=datetime(2026, 7, 20, 9, 45, tzinfo=IST),
        reality_snapshot_reference="deadbeef" * 8,
        dataset_artifact_reference="artifact-001",
        execution_mode=EXECUTION_MODE_LIVE,
    )
    observation_refs = ("OBS-1", "OBS-2")

    lineage = wrap_evidence(raw_evidence, context=context, observation_references=observation_refs)

    assert set(lineage.keys()) == set(raw_evidence.keys())
    for name, item in lineage.items():
        assert isinstance(item, IntelligenceEvidence)
        # Value copied verbatim -- never recomputed or fabricated.
        assert item.value == raw_evidence[name]
        assert item.source_reference == context.reality_snapshot_reference
        assert item.observation_references == observation_refs
        assert item.observed_at == context.as_of_time


def test_regime_brain_evidence_lineage_matches_its_own_evidence_dict():
    closes = [24000 + i * 3 for i in range(10)]
    candles = _candles(closes)
    context = IntelligenceContext(
        as_of_time=datetime(2026, 7, 20, 9, 45, tzinfo=IST),
        reality_snapshot_reference="snap-abc123",
    )
    reading = RegimeBrain().analyze(candles, context)

    assert set(reading.evidence_lineage.keys()) == set(reading.evidence.keys())
    for name, item in reading.evidence_lineage.items():
        assert item.value == reading.evidence[name]
        assert item.source_reference == "snap-abc123"
        assert item.observed_at == context.as_of_time


def test_evidence_lineage_never_fabricates_when_reference_unknown():
    """An honest absence (None), never invented, when the caller has no
    real MarketRealitySnapshot reference to supply."""
    closes = [24000 + i * 3 for i in range(10)]
    candles = _candles(closes)
    context = IntelligenceContext(as_of_time=datetime(2026, 7, 20, 9, 45, tzinfo=IST))
    reading = RegimeBrain().analyze(candles, context)

    for item in reading.evidence_lineage.values():
        assert item.source_reference is None
        assert item.observation_references == ()


# ---------------------------------------------------------------------- #
# Property 5: ATM volatility-reference selection is deterministic
# ---------------------------------------------------------------------- #
def test_atm_strike_selection_is_deterministic_and_matches_live_precedent():
    strikes = [23800, 23900, 24000, 24100, 24200]
    spot = 24030.0

    result_a = select_atm_strike(strikes, spot)
    result_b = select_atm_strike(strikes, spot)
    assert result_a == result_b == 24000  # Nearest to 24030 -- 30 away vs 70 for 24100.

    # Same expression this policy was extracted from, not reinvented
    # (Phase 19.2.1's confirmed precedent: option_chain_adapter.py:122).
    live_precedent = min((s for s in strikes), key=lambda s: abs(s - spot))
    assert result_a == live_precedent


def test_atm_straddle_selection_picks_same_strike_ce_and_pe_same_expiry():
    contracts = [
        (23900, "CE", 220.0), (23900, "PE", 40.0),
        (24000, "CE", 150.0), (24000, "PE", 95.0),
        (24100, "CE", 90.0), (24100, "PE", 160.0),
    ]
    result = select_atm_straddle(spot=24010.0, expiry="2026-07-24", contracts=contracts)

    assert result.policy == VOLATILITY_REFERENCE_ATM_STRADDLE
    assert result.atm_strike == 24000  # Nearest to 24010.
    assert result.ce_premium == 150.0
    assert result.pe_premium == 95.0
    assert result.expiry == "2026-07-24"


def test_atm_straddle_selection_raises_on_incomplete_leg_rather_than_guessing():
    contracts = [(24000, "CE", 150.0)]  # Missing the PE leg entirely.
    with pytest.raises(ValueError, match="missing its CE or PE leg"):
        select_atm_straddle(spot=24000.0, expiry="2026-07-24", contracts=contracts)


def test_atm_strike_selection_raises_on_empty_strikes_rather_than_guessing():
    with pytest.raises(ValueError, match="non-empty"):
        select_atm_strike([], spot=24000.0)
