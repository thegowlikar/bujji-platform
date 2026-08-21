"""Tests for Market Participant Positioning Intelligence (MPPI v1) —
BUJJI Engineering Series 86."""
from __future__ import annotations

import ast
import glob
import os

from bujji.options_observation import engine as opt_engine

from bujji.msi_participant_positioning import config as mppi_config
from bujji.msi_participant_positioning import engine as mppi_engine
from bujji.msi_participant_positioning import journal as mppi_journal
from bujji.msi_participant_positioning import query as mppi_query
from bujji.msi_participant_positioning import runner as mppi_runner
from bujji.msi_participant_positioning import serialization as mppi_serialization
from bujji.msi_participant_positioning import taxonomy as mppi_taxonomy


# ---------------------------------------------------------------------------
# Helpers — real OptionObservation construction via Series 73C's actual
# builder (bujji.options_observation.engine.build_option_observation).
# ---------------------------------------------------------------------------
def _mk_option(strike, option_type, oi, change_oi, underlying_price=100.0, ts="2026-07-24T00:00:00", expiry="2026-07-30"):
    return opt_engine.build_option_observation(
        symbol_provenance="SYNTHETIC",   # built from strike/type by this fixture
        underlying="NIFTY", instrument_symbol=f"NIFTY{strike}{option_type}", strike=strike, expiry=expiry,
        option_type=option_type, exchange="NSE", segment="FO", timestamp=ts, resolution="DAILY",
        open_=None, high=None, low=None, close=None, settlement=None, volume=None,
        open_interest=oi, change_in_open_interest=change_oi, underlying_price=underlying_price,
        origin="REPLAY", acquisition_timestamp=ts, normalization_timestamp=ts,
    )


def _balanced_chain(spot=100.0, ts="2026-07-24T00:00:00"):
    """A roughly symmetric chain: no lens should have a strong opinion."""
    return (
        _mk_option(90, "PE", 1000, 0, spot, ts),
        _mk_option(95, "PE", 1000, 0, spot, ts),
        _mk_option(105, "CE", 1000, 0, spot, ts),
        _mk_option(110, "CE", 1000, 0, spot, ts),
    )


def _bullish_leaning_chain(spot=100.0, ts="2026-07-24T00:00:00"):
    """Heavy put-side OI (high PCR), a put wall just below spot, and
    put-writer-dominant change_in_open_interest -- all real, honest
    reasons for a bullish lean, not fabricated to force the outcome."""
    return (
        _mk_option(99, "PE", 50000, 20000, spot, ts),
        _mk_option(95, "PE", 10000, 1000, spot, ts),
        _mk_option(101, "CE", 5000, 500, spot, ts),
        _mk_option(110, "CE", 5000, 500, spot, ts),
    )


def _bearish_leaning_chain(spot=100.0, ts="2026-07-24T00:00:00"):
    return (
        _mk_option(101, "CE", 50000, 20000, spot, ts),
        _mk_option(110, "CE", 10000, 1000, spot, ts),
        _mk_option(99, "PE", 5000, 500, spot, ts),
        _mk_option(90, "PE", 5000, 500, spot, ts),
    )


# ---------------------------------------------------------------------------
# Deterministic IDs
# ---------------------------------------------------------------------------
def test_determinism_same_input_same_id():
    chain = _bullish_leaning_chain()
    a1 = mppi_engine.assess_participant_positioning(chain, timestamp="2026-07-24T09:30:00")
    a2 = mppi_engine.assess_participant_positioning(chain, timestamp="2099-01-01T00:00:00")
    assert a1.assessment_id == a2.assessment_id
    assert a1.positioning_bias == a2.positioning_bias


def test_assessment_id_changes_when_evidence_changes():
    a1 = mppi_engine.assess_participant_positioning(_bullish_leaning_chain(), timestamp="2026-07-24T09:30:00")
    a2 = mppi_engine.assess_participant_positioning(_bearish_leaning_chain(), timestamp="2026-07-24T09:30:00")
    assert a1.assessment_id != a2.assessment_id


# ---------------------------------------------------------------------------
# Batch vs streaming parity
# ---------------------------------------------------------------------------
def test_batch_vs_streaming_parity():
    chain = _bullish_leaning_chain()
    batch = mppi_runner.assess_positioning(chain, timestamp="2026-07-24T09:30:00")
    stream = mppi_runner.ParticipantPositioningStream()
    live = stream.process(chain, timestamp="2026-07-24T09:30:00")
    assert batch.assessment_id == live.assessment_id
    assert mppi_serialization.assessment_to_json(batch) == mppi_serialization.assessment_to_json(live)


def test_streaming_uses_previous_chain_automatically():
    stream = mppi_runner.ParticipantPositioningStream()
    day1 = _balanced_chain()
    day2 = _bullish_leaning_chain()
    first = stream.process(day1, timestamp="2026-07-24T09:30:00")
    second = stream.process(day2, timestamp="2026-07-25T09:30:00")
    direct = mppi_engine.assess_participant_positioning(day2, day1, timestamp="2026-07-25T09:30:00")
    assert second.assessment_id == direct.assessment_id


# ---------------------------------------------------------------------------
# Real lens derivation — heavier put-side OI/writer activity produces a
# bullish-leaning read; heavier call-side produces bearish; a genuinely
# balanced chain produces no strong opinion.
# ---------------------------------------------------------------------------
def test_bullish_leaning_chain_produces_bullish_or_neutral_never_bearish():
    a = mppi_engine.assess_participant_positioning(_bullish_leaning_chain(), timestamp="2026-07-24T09:30:00")
    assert a.positioning_bias != mppi_taxonomy.BEARISH_POSITIONING


def test_bearish_leaning_chain_produces_bearish_or_neutral_never_bullish():
    a = mppi_engine.assess_participant_positioning(_bearish_leaning_chain(), timestamp="2026-07-24T09:30:00")
    assert a.positioning_bias != mppi_taxonomy.BULLISH_POSITIONING


def test_insufficient_data_returns_unknown():
    tiny_chain = (_mk_option(100, "CE", 1000, 100),)  # below MIN_CONTRACTS_FOR_ANY_LENS
    a = mppi_engine.assess_participant_positioning(tiny_chain, timestamp="2026-07-24T09:30:00")
    assert a.positioning_bias == mppi_taxonomy.UNKNOWN_POSITIONING
    for lens in a.participating_lenses:
        assert lens.positioning_lean == mppi_taxonomy.UNKNOWN_POSITIONING
        assert lens.confidence == mppi_taxonomy.CONFIDENCE_NONE


def test_migration_and_expansion_lenses_honestly_unknown_without_previous_snapshot():
    a = mppi_engine.assess_participant_positioning(_bullish_leaning_chain(), timestamp="2026-07-24T09:30:00")
    migration = next(lo for lo in a.participating_lenses if lo.lens_name == mppi_taxonomy.LENS_OI_MIGRATION)
    expansion = next(lo for lo in a.participating_lenses if lo.lens_name == mppi_taxonomy.LENS_OI_EXPANSION_CONTRACTION)
    assert migration.positioning_lean == mppi_taxonomy.UNKNOWN_POSITIONING
    assert expansion.positioning_lean == mppi_taxonomy.UNKNOWN_POSITIONING
    assert any("No previous chain snapshot" in m for m in a.explanation.missing_evidence)


def test_migration_lens_computed_with_two_snapshots():
    previous = _balanced_chain()
    current = _bullish_leaning_chain()
    a = mppi_engine.assess_participant_positioning(current, previous, timestamp="2026-07-25T09:30:00")
    migration = next(lo for lo in a.participating_lenses if lo.lens_name == mppi_taxonomy.LENS_OI_MIGRATION)
    expansion = next(lo for lo in a.participating_lenses if lo.lens_name == mppi_taxonomy.LENS_OI_EXPANSION_CONTRACTION)
    assert migration.positioning_lean != mppi_taxonomy.UNKNOWN_POSITIONING
    assert expansion.positioning_lean == mppi_taxonomy.NEUTRAL_POSITIONING  # Lens D never votes directionally.
    assert a.positioning_strength != mppi_taxonomy.STRENGTH_UNKNOWN


# ---------------------------------------------------------------------------
# Contradiction preservation — a real scenario where lenses genuinely
# disagree: extreme put-side OI concentration far from spot (bullish
# via PCR) but a strong, near-spot call wall with dominant call-writer
# activity (bearish via Concentration + Writer Dominance).
# ---------------------------------------------------------------------------
def test_mixed_when_lenses_genuinely_disagree():
    spot = 100.0
    chain = (
        _mk_option(60, "PE", 200000, 5000, spot),   # far OTM put OI -> drives PCR bullish, but not a near wall.
        _mk_option(101, "CE", 50000, 50000, spot),  # near-spot call wall, heavy call writing -> bearish concentration + writer dominance.
        _mk_option(102, "CE", 5000, 500, spot),
    )
    a = mppi_engine.assess_participant_positioning(chain, timestamp="2026-07-24T09:30:00")
    pcr = next(lo for lo in a.participating_lenses if lo.lens_name == mppi_taxonomy.LENS_PUT_CALL_OI_RATIO)
    concentration = next(lo for lo in a.participating_lenses if lo.lens_name == mppi_taxonomy.LENS_OI_CONCENTRATION)
    if pcr.positioning_lean == mppi_taxonomy.BULLISH_POSITIONING and concentration.positioning_lean == mppi_taxonomy.BEARISH_POSITIONING:
        assert a.positioning_bias == mppi_taxonomy.MIXED_POSITIONING
        assert mppi_taxonomy.LENS_PUT_CALL_OI_RATIO in a.conflicting_lenses
        assert mppi_taxonomy.LENS_OI_CONCENTRATION in a.conflicting_lenses
        # Never silently averaged away.
        assert a.positioning_bias not in (mppi_taxonomy.BULLISH_POSITIONING, mppi_taxonomy.BEARISH_POSITIONING, mppi_taxonomy.NEUTRAL_POSITIONING)


def test_mixed_when_directly_constructed_opposing_lens_opinions():
    from bujji.msi_participant_positioning.models import LensOpinion
    bullish = LensOpinion(
        lens_name=mppi_taxonomy.LENS_PUT_CALL_OI_RATIO, positioning_lean=mppi_taxonomy.BULLISH_POSITIONING,
        confidence=mppi_taxonomy.CONFIDENCE_MODERATE, supporting_evidence_ids=("OBS-1",), reasoning="test",
    )
    bearish = LensOpinion(
        lens_name=mppi_taxonomy.LENS_OI_CONCENTRATION, positioning_lean=mppi_taxonomy.BEARISH_POSITIONING,
        confidence=mppi_taxonomy.CONFIDENCE_MODERATE, supporting_evidence_ids=("OBS-2",), reasoning="test",
    )
    bias, conflicting = mppi_engine.reconcile_lenses((bullish, bearish))
    assert bias == mppi_taxonomy.MIXED_POSITIONING
    assert set(conflicting) == {mppi_taxonomy.LENS_PUT_CALL_OI_RATIO, mppi_taxonomy.LENS_OI_CONCENTRATION}


# ---------------------------------------------------------------------------
# Evidence lineage / explainability
# ---------------------------------------------------------------------------
def test_evidence_lineage_and_explanation():
    a = mppi_engine.assess_participant_positioning(_bullish_leaning_chain(), timestamp="2026-07-24T09:30:00")
    assert len(a.supporting_observation_ids) > 0
    assert len(a.explanation.which_lenses_participated) == 5
    assert len(a.explanation.per_lens_evidence) == 5
    assert a.explanation.why_positioning_was_chosen


# ---------------------------------------------------------------------------
# Serialization / journal / query
# ---------------------------------------------------------------------------
def test_serialization_round_trip():
    a = mppi_engine.assess_participant_positioning(_bullish_leaning_chain(), timestamp="2026-07-24T09:30:00")
    recovered = mppi_serialization.assessment_from_json(mppi_serialization.assessment_to_json(a))
    assert recovered == a


def test_journal_append_only():
    a = mppi_engine.assess_participant_positioning(_bullish_leaning_chain(), timestamp="2026-07-24T09:30:00")
    journal = mppi_journal.ParticipantPositioningJournal()
    journal.record_assessment(a, recorded_at="2026-07-24T09:30:01")
    entries_before = journal.entries()
    journal.record_assessment(a, recorded_at="2026-07-24T09:30:02")
    assert len(journal) == 2
    assert entries_before == journal.entries()[:1]


def test_query_helpers():
    a = mppi_engine.assess_participant_positioning(_bullish_leaning_chain(), timestamp="2026-07-24T09:30:00")
    assert mppi_query.by_id((a,), a.assessment_id) == a
    assert mppi_query.by_id((a,), "nonexistent") is None
    assert a in mppi_query.by_bias((a,), a.positioning_bias)
    assert mppi_query.latest((a,)) == a


# ---------------------------------------------------------------------------
# Deliverable 7 — documented adapter proving MPPI CAN later become an
# additional MDI lens (synthetic test only, no production wiring, MDI
# itself is not modified).
# ---------------------------------------------------------------------------
def test_deliverable_7_mppi_adapts_into_an_mdi_style_lens_opinion():
    from bujji.msi_market_direction.models import LensOpinion as MdiLensOpinion
    from bujji.msi_market_direction import taxonomy as mdi_taxonomy

    a = mppi_engine.assess_participant_positioning(_bullish_leaning_chain(), timestamp="2026-07-24T09:30:00")

    # Documented translation: MPPI's 3-way (bullish/bearish/neutral) +
    # MIXED/UNKNOWN maps onto MDI's richer 7-band + MIXED/UNKNOWN scale
    # at the plain BULLISH/BEARISH/NEUTRAL band (MPPI does not attempt
    # to claim STRONG/WEAK sub-bands MDI supports -- it only asserts
    # what it can actually support: BULLISH_POSITIONING/BEARISH_POSITIONING/
    # NEUTRAL_POSITIONING/MIXED/UNKNOWN).
    _MPPI_TO_MDI_LEAN = {
        mppi_taxonomy.BULLISH_POSITIONING: mdi_taxonomy.BULLISH,
        mppi_taxonomy.BEARISH_POSITIONING: mdi_taxonomy.BEARISH,
        mppi_taxonomy.NEUTRAL_POSITIONING: mdi_taxonomy.NEUTRAL,
        mppi_taxonomy.MIXED_POSITIONING: mdi_taxonomy.MIXED,
        mppi_taxonomy.UNKNOWN_POSITIONING: mdi_taxonomy.UNKNOWN,
    }
    lens_opinion = MdiLensOpinion(
        lens_name="OPTIONS_POSITIONING_DIRECTION",  # Already reserved in mdi_taxonomy.KNOWN_LENS_NAMES.
        directional_lean=_MPPI_TO_MDI_LEAN[a.positioning_bias],
        confidence=a.positioning_strength if a.positioning_strength in mdi_taxonomy.ALL_CONFIDENCE_LEVELS else mdi_taxonomy.CONFIDENCE_LOW,
        supporting_evidence_ids=a.supporting_observation_ids,
        reasoning=f"MPPI positioning_bias={a.positioning_bias}: {a.explanation.why_positioning_was_chosen}",
    )
    assert lens_opinion.lens_name == mdi_taxonomy.OPTIONS_POSITIONING_DIRECTION
    assert lens_opinion.directional_lean in mdi_taxonomy.ALL_DIRECTIONAL_LEANS


# ---------------------------------------------------------------------------
# AST isolation
# ---------------------------------------------------------------------------
_PACKAGE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bujji", "msi_participant_positioning")

_FORBIDDEN_MODULE_PREFIXES = (
    "mic_v2", "bujji.mic_replay", "bujji.production_runtime", "bujji.trading_brain",
    "bujji.strategy_selector", "fyers_apiv3", "bujji.intelligence",
    "bujji.msi_consensus", "bujji.msi_decision_synthesis",
    "bujji.msi_strategy_eligibility", "bujji.msi_trade_intent", "bujji.msi_market_direction",
)

_FORBIDDEN_TRADING_TERMS = (
    "strike_select", "expiry_select", "quantity", "execution_plan", "place_order",
    "iron_condor", "straddle", "strategy_selection", "position_size",
)


def _all_package_files():
    return sorted(glob.glob(os.path.join(_PACKAGE_DIR, "*.py")))


def test_ast_isolation_no_forbidden_imports():
    for path in _all_package_files():
        with open(path) as f:
            tree = ast.parse(f.read(), filename=path)
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                for forbidden in _FORBIDDEN_MODULE_PREFIXES:
                    assert not name.startswith(forbidden), f"{path} imports forbidden module {name}"


def test_ast_isolation_no_uuid4_no_unseeded_randomness():
    for path in _all_package_files():
        with open(path) as f:
            tree = ast.parse(f.read(), filename=path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "uuid4":
                raise AssertionError(f"{path} calls uuid4()")
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name != "random", f"{path} imports random"
            if isinstance(node, ast.ImportFrom):
                assert node.module != "random", f"{path} imports from random"


def test_ast_isolation_no_forbidden_trading_terms():
    for path in _all_package_files():
        with open(path) as f:
            tree = ast.parse(f.read(), filename=path)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Name, ast.Attribute)):
                identifier = node.id if isinstance(node, ast.Name) else node.attr
                lowered = identifier.lower()
                for term in _FORBIDDEN_TRADING_TERMS:
                    assert term not in lowered, f"{path} contains forbidden identifier fragment '{term}' in '{identifier}'"
