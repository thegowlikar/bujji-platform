"""Tests for bujji.msi_trade_construction — Engineering Series 90."""
from __future__ import annotations

import ast
import os

import pytest

from bujji.msi_trade_construction import config as tc_config
from bujji.msi_trade_construction import engine, taxonomy
from bujji.msi_trade_construction.journal import TradeConstructionJournal
from bujji.msi_trade_construction.models import TradeConstructionAssessment
from bujji.msi_trade_construction.runner import construct_trades_batch, TradeConstructionStream
from bujji.msi_trade_construction import query as tc_query
from bujji.msi_trade_construction import serialization as tc_serialization
from bujji.options_observation import runner as opt_runner

REAL_BHAVCOPY = "/tmp/m1/BhavCopy_NSE_FO_0_0_0_20260525_F_0000.csv"
DAY = "2026-05-25"
TS = "2026-05-25T15:30:00+05:30"


def _real_chain():
    with open(REAL_BHAVCOPY) as f:
        text = f.read()
    series, _ = opt_runner.ingest_all_option_series_from_bhavcopy(text, DAY, underlying="NIFTY")
    return tuple(s.observations()[-1] for s in series if len(s.observations()) > 0)


@pytest.fixture(scope="module")
def chain():
    return _real_chain()


@pytest.fixture(scope="module")
def spot(chain):
    return next((r.underlying_price for r in chain if r.underlying_price), None)


# --- Deliverable 2: Expiry selection ---------------------------------------

def test_select_expiry_chooses_nearest_within_window(chain):
    d = engine.select_expiry(chain, DAY, min_dte=1, max_dte=45)
    assert d.chosen_expiry is not None
    assert 1 <= d.dte <= 45
    assert d.chosen_expiry in d.candidate_expiries


def test_select_expiry_reports_rejected_expiries_honestly(chain):
    d = engine.select_expiry(chain, DAY, min_dte=1, max_dte=10)
    assert d.rejected_expiries  # some real expiries are beyond 10 DTE
    assert all(reason in (taxonomy.EXPIRY_REJECTED_BELOW_MIN_DTE, taxonomy.EXPIRY_REJECTED_ABOVE_MAX_DTE)
               for _, reason in d.rejected_expiries)


def test_select_expiry_empty_chain_returns_none_honestly():
    d = engine.select_expiry((), DAY)
    assert d.chosen_expiry is None
    assert d.candidate_expiries == ()


def test_calendar_expiry_selection_picks_two_distinct_expiries(chain):
    near, far = engine.select_calendar_expiries(chain, DAY, min_dte=1, max_dte=45)
    assert near.chosen_expiry is not None
    assert far is not None
    assert far > near.chosen_expiry


# --- Deliverable 3/4: construction across all supported families ----------

@pytest.mark.parametrize("family", taxonomy.SUPPORTED_FAMILIES)
def test_all_supported_families_construct_on_a_real_day(chain, spot, family):
    a = engine.construct_trade(
        family, chain, spot, DAY, direction="BULLISH", expected_move_pct=1.2, timestamp=TS,
    )
    assert isinstance(a, TradeConstructionAssessment)
    assert a.constructed is True, f"{family} unexpectedly rejected: {a.rejection_reason}"
    assert len(a.legs) >= 1
    assert a.risk_profile in taxonomy.ALL_RISK_PROFILES
    assert a.risk_profile != taxonomy.RISK_UNKNOWN


def test_directional_families_flip_side_with_direction(chain, spot):
    bullish = engine.construct_trade("LONG_DIRECTIONAL", chain, spot, DAY, direction="BULLISH", timestamp=TS)
    bearish = engine.construct_trade("LONG_DIRECTIONAL", chain, spot, DAY, direction="BEARISH", timestamp=TS)
    assert bullish.legs[0].option_type == "CE"
    assert bearish.legs[0].option_type == "PE"


def test_unknown_direction_fails_closed_for_directional_family(chain, spot):
    a = engine.construct_trade("LONG_DIRECTIONAL", chain, spot, DAY, direction=None, timestamp=TS)
    assert a.constructed is False
    assert a.rejection_reason == taxonomy.REJECT_STRIKE_UNAVAILABLE


def test_unsupported_family_fails_closed(chain, spot):
    a = engine.construct_trade("NOT_A_REAL_FAMILY", chain, spot, DAY, timestamp=TS)
    assert a.constructed is False
    assert a.rejection_reason == taxonomy.REJECT_UNSUPPORTED_FAMILY


def test_empty_chain_fails_closed(spot):
    a = engine.construct_trade("LONG_DIRECTIONAL", (), spot, DAY, direction="BULLISH", timestamp=TS)
    assert a.constructed is False
    assert a.rejection_reason == taxonomy.REJECT_INCONSISTENT_CHAIN


def test_no_spot_fails_closed(chain):
    a = engine.construct_trade("LONG_DIRECTIONAL", chain, None, DAY, direction="BULLISH", timestamp=TS)
    assert a.constructed is False
    assert a.rejection_reason == taxonomy.REJECT_INCONSISTENT_CHAIN


def test_impossibly_narrow_dte_window_fails_closed(chain, spot):
    a = engine.construct_trade(
        "LONG_DIRECTIONAL", chain, spot, DAY, direction="BULLISH", timestamp=TS,
        min_dte=1000, max_dte=1001,
    )
    assert a.constructed is False
    assert a.rejection_reason == taxonomy.REJECT_NO_SUITABLE_EXPIRY


def test_liquidity_insufficient_fails_closed_when_oi_threshold_unreachable(chain, spot, monkeypatch):
    monkeypatch.setattr(tc_config, "MIN_OPEN_INTEREST", 10**12)
    a = engine.construct_trade("LONG_DIRECTIONAL", chain, spot, DAY, direction="BULLISH", timestamp=TS)
    assert a.constructed is False
    assert a.rejection_reason == taxonomy.REJECT_LIQUIDITY_INSUFFICIENT


# --- Deliverable 5: required_margin always disclosed None, never guessed --

def test_required_margin_is_always_none_with_disclosed_reason(chain, spot):
    a = engine.construct_trade("LONG_DIRECTIONAL", chain, spot, DAY, direction="BULLISH", timestamp=TS)
    assert a.required_margin is None
    assert a.margin_unavailable_reason is not None
    assert "SPAN" in a.margin_unavailable_reason or "margin" in a.margin_unavailable_reason.lower()


# --- Deliverable 7: explainability ------------------------------------------

def test_explanation_answers_all_required_questions(chain, spot):
    a = engine.construct_trade("IRON_CONDOR", chain, spot, DAY, direction="BULLISH", expected_move_pct=1.2, timestamp=TS)
    assert a.explanation.why_this_expiry
    assert a.explanation.why_these_strikes
    assert a.explanation.dominant_constraints
    # Neighbouring-strike rejection explanations exist for at least one leg.
    assert a.explanation.why_not_neighbouring_strikes


def test_reasoning_never_cites_historical_performance(chain, spot):
    forbidden = ("performed best", "historically", "backtest", "pnl", "profit")
    for family in taxonomy.SUPPORTED_FAMILIES:
        a = engine.construct_trade(family, chain, spot, DAY, direction="BULLISH", expected_move_pct=1.2, timestamp=TS)
        text = " ".join(a.explanation.why_these_strikes + a.explanation.why_this_expiry).lower()
        for word in forbidden:
            assert word not in text, f"{family} explanation cites forbidden phrase: {word}"


# --- Deliverable 8: determinism ---------------------------------------------

def test_determinism_identical_input_identical_id(chain, spot):
    a1 = engine.construct_trade("IRON_CONDOR", chain, spot, DAY, direction="BULLISH", expected_move_pct=1.2, timestamp=TS)
    a2 = engine.construct_trade("IRON_CONDOR", chain, spot, DAY, direction="BULLISH", expected_move_pct=1.2, timestamp=TS)
    assert a1.assessment_id == a2.assessment_id


def test_full_corpus_family_replay_byte_identical_on_rerun(chain, spot):
    def run():
        return tuple(
            engine.construct_trade(f, chain, spot, DAY, direction="BULLISH", expected_move_pct=1.2, timestamp=TS)
            for f in taxonomy.SUPPORTED_FAMILIES
        )
    r1, r2 = run(), run()
    assert [a.assessment_id for a in r1] == [a.assessment_id for a in r2]


def test_assessment_is_immutable(chain, spot):
    a = engine.construct_trade("LONG_DIRECTIONAL", chain, spot, DAY, direction="BULLISH", timestamp=TS)
    with pytest.raises(Exception):
        a.constructed = False


# --- Batch/streaming parity -------------------------------------------------

def test_batch_and_streaming_are_byte_identical(chain, spot):
    requests = [
        dict(strategy_family=f, chain=chain, spot=spot, as_of_date=DAY, direction="BULLISH",
             expected_move_pct=1.2, timestamp=TS)
        for f in taxonomy.SUPPORTED_FAMILIES
    ]
    batch = construct_trades_batch(requests)
    stream = TradeConstructionStream()
    streamed = tuple(stream.submit(**r) for r in requests)
    assert [a.assessment_id for a in batch] == [a.assessment_id for a in streamed]
    assert len(stream.journal) == len(requests)


# --- Serialization / query --------------------------------------------------

def test_serialization_round_trip_preserves_fields(chain, spot):
    a = engine.construct_trade("IRON_CONDOR", chain, spot, DAY, direction="BULLISH", expected_move_pct=1.2, timestamp=TS)
    d = tc_serialization.assessment_to_dict(a)
    assert d["assessment_id"] == a.assessment_id
    assert d["strategy_family"] == "IRON_CONDOR"
    assert len(d["legs"]) == len(a.legs)


def test_query_helpers(chain, spot):
    assessments = tuple(
        engine.construct_trade(f, chain, spot, DAY, direction="BULLISH", expected_move_pct=1.2, timestamp=TS)
        for f in taxonomy.SUPPORTED_FAMILIES
    )
    a0 = assessments[0]
    assert tc_query.by_id(assessments, a0.assessment_id) is a0
    assert tc_query.by_strategy_family(assessments, "IRON_CONDOR")
    assert all(a.constructed for a in tc_query.constructed_only(assessments))


def test_journal_is_append_only(chain, spot):
    j = TradeConstructionJournal()
    a = engine.construct_trade("LONG_DIRECTIONAL", chain, spot, DAY, direction="BULLISH", timestamp=TS)
    j.record_assessment(a, recorded_at=TS)
    assert len(j) == 1
    j.record_assessment(a, recorded_at=TS)
    assert len(j) == 2


# --- AST isolation (house convention) ---------------------------------------

def test_ast_no_forbidden_imports_or_constructs():
    pkg_dir = os.path.join(os.path.dirname(__file__), "..", "bujji", "msi_trade_construction")
    forbidden_modules = ("mic_v2", "bujji.production_runtime", "bujji.trading_brain", "fyers_apiv3")
    forbidden_identifiers = ("optimi", "backtest", "pnl", "historical_return")
    for fname in os.listdir(pkg_dir):
        if not fname.endswith(".py"):
            continue
        path = os.path.join(pkg_dir, fname)
        with open(path) as f:
            source = f.read()
        tree = ast.parse(source, filename=fname)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not any(alias.name.startswith(m) for m in forbidden_modules), f"{fname} imports {alias.name}"
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not any(node.module.startswith(m) for m in forbidden_modules), f"{fname} imports from {node.module}"
            if isinstance(node, (ast.Name, ast.Attribute)):
                identifier = node.id if isinstance(node, ast.Name) else node.attr
                lowered = identifier.lower()
                for ident in forbidden_identifiers:
                    assert ident not in lowered, f"{fname} contains forbidden identifier fragment '{ident}' in '{identifier}'"
                assert lowered != "uuid4", f"{fname} calls uuid4()"


def test_ast_no_unseeded_randomness():
    import random  # noqa: F401 -- imported only to reference module name below
    pkg_dir = os.path.join(os.path.dirname(__file__), "..", "bujji", "msi_trade_construction")
    for fname in os.listdir(pkg_dir):
        if not fname.endswith(".py"):
            continue
        with open(os.path.join(pkg_dir, fname)) as f:
            source = f.read()
        assert "random." not in source, f"{fname} uses randomness"
        assert "datetime.now(" not in source, f"{fname} uses wall-clock now()"
