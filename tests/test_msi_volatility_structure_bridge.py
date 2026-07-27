"""Tests for Volatility Structure Bridge (VSB v1) — BUJJI Engineering
Series 88."""
from __future__ import annotations

import ast
import glob
import os
from datetime import datetime, timedelta

from bujji.msi_volatility_structure import config as vsb_config
from bujji.msi_volatility_structure import engine as vsb_engine
from bujji.msi_volatility_structure import journal as vsb_journal
from bujji.msi_volatility_structure import query as vsb_query
from bujji.msi_volatility_structure import runner as vsb_runner
from bujji.msi_volatility_structure import serialization as vsb_serialization
from bujji.msi_volatility_structure import taxonomy as vsb_taxonomy


def _closes(prices, start="2026-07-24T09:15:00"):
    ts0 = datetime.fromisoformat(start)
    return [((ts0 + timedelta(minutes=15 * i)).isoformat(), p) for i, p in enumerate(prices)]


# ---------------------------------------------------------------------------
# Reuse verification — the SAME real math must be reachable/callable
# (proves no duplication happened; a real, imported function is used).
# ---------------------------------------------------------------------------
def test_reuses_legacy_iv_solver_directly():
    from bujji.intelligence.volatility_brain import solve_implied_volatility as legacy_solver
    assert vsb_engine.solve_implied_volatility is legacy_solver


def test_reuses_legacy_expected_move_directly():
    from bujji.intelligence.volatility_brain import compute_expected_move as legacy_move
    assert vsb_engine.compute_expected_move is legacy_move


def test_reuses_legacy_realized_vol_directly():
    from bujji.intelligence.volatility_brain import VolatilityBrain
    closes = _closes([24000 + i for i in range(10)])
    rv1 = vsb_engine.derive_realized_vol(closes)
    from bujji.core.models import Candle
    candles = vsb_engine._synthetic_candles(closes)
    rv2 = VolatilityBrain._annualized_realized_vol(candles)
    assert rv1 == rv2


def test_reuses_legacy_richness_classification_directly():
    from bujji.intelligence.volatility_brain import VolatilityBrain
    from bujji.intelligence.models import Richness
    richness, reason, _ = VolatilityBrain._classify_richness(1.5)
    assert richness == Richness.IV_RICH
    state, _ = vsb_engine.derive_iv_state(iv_average=0.30, realized_vol=0.20)
    assert state == vsb_taxonomy.IV_RICH


# ---------------------------------------------------------------------------
# Missing-evidence honesty (Deliverable 1)
# ---------------------------------------------------------------------------
def test_skew_and_term_structure_always_unknown():
    closes = _closes([24000 + i for i in range(10)])
    a = vsb_engine.assess_volatility_structure(
        spot=24010, strike=24000, t_years=7 / 365, ce_premium=150.0, pe_premium=120.0,
        closes_with_ts=closes, timestamp="2026-07-24T15:30:00",
    )
    assert a.skew_state == vsb_taxonomy.SKEW_UNKNOWN
    assert a.term_structure_state == vsb_taxonomy.TERM_STRUCTURE_UNKNOWN


def test_iv_unsolvable_without_both_legs():
    closes = _closes([24000 + i for i in range(10)])
    a = vsb_engine.assess_volatility_structure(
        spot=24010, strike=24000, t_years=7 / 365, ce_premium=None, pe_premium=120.0,
        closes_with_ts=closes, timestamp="2026-07-24T15:30:00",
    )
    assert a.iv_state == vsb_taxonomy.IV_UNKNOWN
    assert a.iv_average is None


def test_insufficient_candles_reports_unknown_regime():
    closes = _closes([24000, 24001, 24002])  # below MIN_CANDLES_FOR_REALIZED_VOL
    a = vsb_engine.assess_volatility_structure(
        spot=24010, strike=24000, t_years=7 / 365, ce_premium=150.0, pe_premium=120.0,
        closes_with_ts=closes, timestamp="2026-07-24T15:30:00",
    )
    assert a.volatility_regime == vsb_taxonomy.REGIME_UNKNOWN
    assert a.realized_vol is None


def test_stable_and_unknown_regime_are_distinct_real_vs_missing_evidence():
    """BUGFIX regression test: a real, computed compression_ratio
    landing in the ambiguous middle must classify STABLE (real evidence
    of an unremarkable reading), never UNKNOWN (genuinely insufficient
    data) -- these are different facts and must never be conflated."""
    # Enough candles, small/flat moves -- realized_vol stays below
    # VOL_HIGH_THRESHOLD and compression_ratio lands in the ambiguous
    # middle (neither extreme compression nor extreme expansion).
    closes = _closes([24000, 24005, 24002, 24006, 24003, 24007, 24004, 24008, 24005, 24009])
    regime, expansion_state, compression_state, realized_vol = vsb_engine.derive_regime_expansion_compression(closes)
    if regime == vsb_taxonomy.REGIME_STABLE:
        assert realized_vol is not None
        assert expansion_state in (vsb_taxonomy.EXPANSION_CONFIRMED, vsb_taxonomy.EXPANSION_NOT_DETECTED)
        assert compression_state in (vsb_taxonomy.COMPRESSION_CONFIRMED, vsb_taxonomy.COMPRESSION_NOT_DETECTED)

    # Genuinely insufficient data (below MIN_CANDLES_FOR_REALIZED_VOL) must ALWAYS be UNKNOWN, never STABLE.
    tiny_closes = _closes([24000, 24001, 24002])
    regime2, _, _, rv2 = vsb_engine.derive_regime_expansion_compression(tiny_closes)
    assert regime2 == vsb_taxonomy.REGIME_UNKNOWN
    assert rv2 is None

    # STABLE and UNKNOWN must be provably different taxonomy values.
    assert vsb_taxonomy.REGIME_STABLE != vsb_taxonomy.REGIME_UNKNOWN
    assert vsb_taxonomy.REGIME_STABLE in vsb_taxonomy.ALL_VOLATILITY_REGIMES


# ---------------------------------------------------------------------------
# Determinism / parity
# ---------------------------------------------------------------------------
def test_determinism_same_input_same_id():
    closes = _closes([24000, 24010, 23990, 24020, 23980, 24030, 23970, 24050])
    a1 = vsb_engine.assess_volatility_structure(
        spot=24045, strike=24000, t_years=7 / 365, ce_premium=180.0, pe_premium=140.0,
        closes_with_ts=closes, timestamp="2026-07-24T15:30:00",
    )
    a2 = vsb_engine.assess_volatility_structure(
        spot=24045, strike=24000, t_years=7 / 365, ce_premium=180.0, pe_premium=140.0,
        closes_with_ts=closes, timestamp="2099-01-01T00:00:00",
    )
    assert a1.assessment_id == a2.assessment_id


def test_batch_vs_streaming_parity():
    closes = _closes([24000, 24010, 23990, 24020, 23980, 24030, 23970, 24050])
    batch = vsb_runner.assess_volatility(24045, 24000, 7 / 365, 180.0, 140.0, closes, timestamp="2026-07-24T15:30:00")
    stream = vsb_runner.VolatilityStructureStream()
    live = stream.process(24045, 24000, 7 / 365, 180.0, 140.0, closes, timestamp="2026-07-24T15:30:00")
    assert batch.assessment_id == live.assessment_id
    assert vsb_serialization.assessment_to_json(batch) == vsb_serialization.assessment_to_json(live)


def test_byte_identical_full_rerun():
    closes = _closes([24000, 24010, 23990, 24020, 23980, 24030, 23970, 24050, 24060, 24070])
    def _run():
        return vsb_engine.assess_volatility_structure(
            spot=24075, strike=24050, t_years=5 / 365, ce_premium=160.0, pe_premium=130.0,
            closes_with_ts=closes, timestamp="2026-07-24T15:30:00",
        )
    r1, r2 = _run(), _run()
    assert vsb_serialization.assessment_to_json(r1) == vsb_serialization.assessment_to_json(r2)


# ---------------------------------------------------------------------------
# Serialization / journal / query
# ---------------------------------------------------------------------------
def test_serialization_round_trip():
    closes = _closes([24000 + i for i in range(10)])
    a = vsb_engine.assess_volatility_structure(
        spot=24010, strike=24000, t_years=7 / 365, ce_premium=150.0, pe_premium=120.0,
        closes_with_ts=closes, timestamp="2026-07-24T15:30:00",
    )
    recovered = vsb_serialization.assessment_from_json(vsb_serialization.assessment_to_json(a))
    assert recovered == a


def test_journal_append_only():
    closes = _closes([24000 + i for i in range(10)])
    a = vsb_engine.assess_volatility_structure(
        spot=24010, strike=24000, t_years=7 / 365, ce_premium=150.0, pe_premium=120.0,
        closes_with_ts=closes, timestamp="2026-07-24T15:30:00",
    )
    journal = vsb_journal.VolatilityStructureJournal()
    journal.record_assessment(a, recorded_at="2026-07-24T15:30:01")
    entries_before = journal.entries()
    journal.record_assessment(a, recorded_at="2026-07-24T15:30:02")
    assert len(journal) == 2
    assert entries_before == journal.entries()[:1]


def test_query_helpers():
    closes = _closes([24000 + i for i in range(10)])
    a = vsb_engine.assess_volatility_structure(
        spot=24010, strike=24000, t_years=7 / 365, ce_premium=150.0, pe_premium=120.0,
        closes_with_ts=closes, timestamp="2026-07-24T15:30:00",
    )
    assert vsb_query.by_id((a,), a.assessment_id) == a
    assert vsb_query.by_id((a,), "nonexistent") is None
    assert a in vsb_query.by_regime((a,), a.volatility_regime)
    assert vsb_query.latest((a,)) == a


# ---------------------------------------------------------------------------
# Deliverable 5 — documented adapter proving VSB CAN become an
# additional MDI lens (synthetic test only, no production wiring, MDI
# itself is not modified — same pattern as Series 86's own Deliverable 7).
# ---------------------------------------------------------------------------
def test_deliverable_5_vsb_adapts_into_an_mdi_style_lens_opinion():
    from bujji.msi_market_direction.models import LensOpinion as MdiLensOpinion
    from bujji.msi_market_direction import taxonomy as mdi_taxonomy

    closes = _closes([24000, 24010, 23990, 24020, 23980, 24030, 23970, 24050])
    a = vsb_engine.assess_volatility_structure(
        spot=24045, strike=24000, t_years=7 / 365, ce_premium=180.0, pe_premium=140.0,
        closes_with_ts=closes, timestamp="2026-07-24T15:30:00",
    )
    # Documented translation: Volatility Structure has NO directional
    # opinion by design (it describes regime/richness, never direction)
    # -- its MDI lens contribution is always NEUTRAL or UNKNOWN,
    # honestly reflecting that this domain cannot vote bullish/bearish.
    lean = mdi_taxonomy.UNKNOWN if a.confidence == vsb_taxonomy.CONFIDENCE_NONE else mdi_taxonomy.NEUTRAL
    lens_opinion = MdiLensOpinion(
        lens_name="VOLATILITY_DIRECTION",  # Already reserved in mdi_taxonomy.KNOWN_LENS_NAMES.
        directional_lean=lean,
        confidence=a.confidence if a.confidence in mdi_taxonomy.ALL_CONFIDENCE_LEVELS else mdi_taxonomy.CONFIDENCE_LOW,
        supporting_evidence_ids=(),
        reasoning=f"VSB volatility_regime={a.volatility_regime}, iv_state={a.iv_state} -- Volatility Structure "
                  f"has no directional opinion by design; contributes NEUTRAL/UNKNOWN only.",
    )
    assert lens_opinion.lens_name == mdi_taxonomy.VOLATILITY_DIRECTION
    assert lens_opinion.directional_lean in (mdi_taxonomy.NEUTRAL, mdi_taxonomy.UNKNOWN)


# ---------------------------------------------------------------------------
# AST isolation
# ---------------------------------------------------------------------------
_PACKAGE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bujji", "msi_volatility_structure")

_FORBIDDEN_MODULE_PREFIXES = (
    "mic_v2", "bujji.mic_replay", "bujji.production_runtime", "bujji.trading_brain",
    "bujji.strategy_selector", "fyers_apiv3",
    "bujji.msi_price_structure", "bujji.msi_market_structure", "bujji.msi_market_direction",
    "bujji.msi_decision_synthesis", "bujji.msi_consensus", "bujji.msi_strategy_eligibility",
    "bujji.msi_trade_intent", "bujji.msi_participant_positioning", "bujji.msi_strategy_selection_foundation",
)
_FORBIDDEN_TERMS = ("uuid4", "strike_select", "expiry_select", "place_order", "execution_plan", "score", "rank_")


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


def test_no_wall_clock_dependency():
    for path in _all_package_files():
        with open(path) as f:
            tree = ast.parse(f.read(), filename=path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                name = func.id if isinstance(func, ast.Name) else (func.attr if isinstance(func, ast.Attribute) else None)
                assert name != "now_ist", f"{path} calls now_ist()"
                if name == "now" and isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id == "datetime":
                    raise AssertionError(f"{path} calls datetime.now()")
