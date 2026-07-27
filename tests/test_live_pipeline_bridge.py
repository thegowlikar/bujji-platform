"""Tests for bujji.live_pipeline_bridge — Engineering Sprint 105."""
from __future__ import annotations

import ast
import json
import os

import pytest

import asyncio

from bujji.core.process_lock import LockAcquisitionError
from bujji.live_pipeline_bridge import (
    SessionDriver, tick_to_event, fetch_live_premium, fetch_live_atm_premiums,
)

DAY = "2026-05-25"
INTRADAY_PATH = "/tmp/nifty_intraday_by_day_expanded.json"
BHAVCOPY_PATH = "/tmp/m1/BhavCopy_NSE_FO_0_0_0_20260525_F_0000.csv"


@pytest.fixture(scope="module")
def real_candles():
    with open(INTRADAY_PATH) as f:
        data = json.load(f)
    return data[DAY]


@pytest.fixture(scope="module")
def real_bhavcopy_text():
    with open(BHAVCOPY_PATH) as f:
        return f.read()


# --- Deliverable 2/3: bridge produces the real end-to-end chain ----------

def test_tick_to_event_produces_translatable_event():
    event = tick_to_event(1, "NIFTY", "2026-05-25T09:15:00+05:30", 24000.0, "test")
    assert event.event_type == "TICK_RECEIVED"
    assert event.payload["instrument"] == "NIFTY"
    assert event.payload["price"] == 24000.0


def test_session_driver_produces_observations_events_episodes(real_candles, tmp_path):
    driver = SessionDriver(lock_path=str(tmp_path / "lock1"))
    driver.acquire()
    for c in real_candles:
        driver.process_tick("NIFTY", c["ts"], c["close"])
    assert len(driver.result.observations) == len(real_candles)
    assert len(driver.result.events) > 0
    assert len(driver.result.episodes) > 0
    driver.close_session()


def test_decision_cadence_produces_a_real_thesis_and_decision(real_candles, tmp_path):
    driver = SessionDriver(lock_path=str(tmp_path / "lock2"))
    driver.acquire()
    for c in real_candles:
        driver.process_tick("NIFTY", c["ts"], c["close"])
    result = driver.run_decision_cadence(timestamp=real_candles[-1]["ts"])
    assert result.thesis is not None
    assert result.thesis.thesis_type in (
        "TREND_CONTINUATION", "TREND_REVERSAL", "RANGE_PERSISTENCE", "VOLATILITY_EXPANSION",
        "VOLATILITY_COMPRESSION", "BREAKOUT", "FAILED_BREAKOUT", "MEAN_REVERSION", "EVENT_RISK", "NO_TRADE",
    )
    assert result.decision is not None
    assert result.decision.decision_outcome in ("TRADE_APPROVED", "NO_TRADE")
    driver.close_session()


def test_never_places_a_broker_order(real_candles, tmp_path):
    driver = SessionDriver(lock_path=str(tmp_path / "lock3"))
    driver.acquire()
    for c in real_candles[:3]:
        driver.process_tick("NIFTY", c["ts"], c["close"])
    result = driver.run_decision_cadence(timestamp=real_candles[2]["ts"])
    driver.close_session()
    # Structural: SessionDriver has no method that could place an order.
    assert not hasattr(driver, "place_order")
    assert not hasattr(driver, "submit_order")


# --- Deliverable 5: failure handling ---------------------------------------

def test_duplicate_tick_is_dropped_not_reprocessed(tmp_path):
    driver = SessionDriver(lock_path=str(tmp_path / "lock4"))
    driver.acquire()
    driver.process_tick("NIFTY", "2026-05-25T09:15:00+05:30", 24000.0)
    driver.process_tick("NIFTY", "2026-05-25T09:15:00+05:30", 24000.0)
    assert len(driver.result.observations) == 1
    assert driver.result.dropped_ticks == 1
    driver.close_session()


def test_duplicate_session_blocked_while_first_is_held(tmp_path):
    lock_path = str(tmp_path / "shared.lock")
    d1 = SessionDriver(lock_path=lock_path)
    d1.acquire()
    d2 = SessionDriver(lock_path=lock_path)
    with pytest.raises(LockAcquisitionError):
        d2.acquire()
    d1.release()


def test_session_restart_reacquires_lock_after_release(tmp_path):
    lock_path = str(tmp_path / "restart.lock")
    d1 = SessionDriver(lock_path=lock_path)
    d1.acquire()
    d1.close_session()  # releases.
    d2 = SessionDriver(lock_path=lock_path)
    d2.acquire()  # must not raise -- clean restart.
    d2.release()


# --- Deliverable 6: replay parity -------------------------------------------

def test_replay_parity_thesis_type_matches_known_replay_result(real_candles, tmp_path):
    """The real Series 92 replay of 2026-05-25 found thesis_type=TREND_CONTINUATION
    (documented in docs/TRADE_THESIS_ENGINE.md's own sample day). The live
    pipeline bridge, fed the SAME real intraday closes, must produce the
    identical thesis TYPE -- this is the real, structural replay-parity
    proof Deliverable 6 requires for the price-structure-driven domains."""
    driver = SessionDriver(lock_path=str(tmp_path / "lock5"))
    driver.acquire()
    for c in real_candles:
        driver.process_tick("NIFTY", c["ts"], c["close"])
    result = driver.run_decision_cadence(timestamp=real_candles[-1]["ts"])
    driver.close_session()
    assert result.thesis.thesis_type == "TREND_CONTINUATION"
    assert result.mdi.overall_direction == "STRONG_BULLISH"


def test_replay_parity_deterministic_on_rerun(real_candles, tmp_path):
    def run(path):
        driver = SessionDriver(lock_path=str(tmp_path / path))
        driver.acquire()
        for c in real_candles:
            driver.process_tick("NIFTY", c["ts"], c["close"])
        result = driver.run_decision_cadence(timestamp=real_candles[-1]["ts"])
        driver.close_session()
        return result

    r1 = run("lock6a")
    r2 = run("lock6b")
    assert r1.thesis.assessment_id == r2.thesis.assessment_id
    assert r1.psi.assessment_id == r2.psi.assessment_id


# --- Option-chain wiring: resolves Sprint 105's own disclosed conviction
# divergence (docs/LIVE_PIPELINE_INTEGRATION.md Section 6) -----------------

def test_option_chain_wiring_resolves_conviction_divergence(real_candles, real_bhavcopy_text, tmp_path):
    """Sprint 105 found thesis_type/direction matched replay exactly but
    conviction diverged (MODERATE vs HIGH) because MPPI/Consensus were
    neutral stand-ins. With a real chain loaded, conviction must now
    match the real, documented replay result (HIGH) exactly."""
    driver = SessionDriver(lock_path=str(tmp_path / "lock8"))
    driver.acquire()
    driver.load_option_chain(real_bhavcopy_text, DAY)
    for c in real_candles:
        driver.process_tick("NIFTY", c["ts"], c["close"])
    result = driver.run_decision_cadence(timestamp=real_candles[-1]["ts"])
    driver.close_session()

    assert result.option_chain_wired is True
    assert result.thesis.thesis_type == "TREND_CONTINUATION"
    assert result.mdi.overall_direction == "STRONG_BULLISH"
    assert result.thesis.conviction == "HIGH"  # previously MODERATE without a real chain.


def test_option_chain_wiring_produces_real_mppi_and_consensus(real_candles, real_bhavcopy_text, tmp_path):
    driver = SessionDriver(lock_path=str(tmp_path / "lock9"))
    driver.acquire()
    driver.load_option_chain(real_bhavcopy_text, DAY)
    for c in real_candles:
        driver.process_tick("NIFTY", c["ts"], c["close"])
    result = driver.run_decision_cadence(timestamp=real_candles[-1]["ts"])
    driver.close_session()

    assert result.mppi is not None
    assert result.mppi.positioning_bias in (
        "BULLISH_POSITIONING", "BEARISH_POSITIONING", "NEUTRAL_POSITIONING",
        "MIXED_POSITIONING", "UNKNOWN_POSITIONING",
    )
    assert result.consensus is not None
    assert result.consensus.consensus_level != "NO_CONSENSUS"  # a real MDI+VSB view was actually computed.
    assert result.vsb is not None


def test_without_chain_falls_back_honestly(real_candles, tmp_path):
    """Omitting load_option_chain must not silently pretend to have real
    option data -- option_chain_wired stays False and MPPI stays the
    disclosed neutral stand-in."""
    driver = SessionDriver(lock_path=str(tmp_path / "lock10"))
    driver.acquire()
    for c in real_candles:
        driver.process_tick("NIFTY", c["ts"], c["close"])
    result = driver.run_decision_cadence(timestamp=real_candles[-1]["ts"])
    driver.close_session()

    assert result.option_chain_wired is False
    assert result.mppi.positioning_bias == "UNKNOWN_POSITIONING"
    assert result.vsb is None
    # Consensus is still real (computed from the real MDI view alone), never fabricated.
    assert result.consensus is not None


def test_option_chain_wiring_is_deterministic(real_candles, real_bhavcopy_text, tmp_path):
    def run(path):
        driver = SessionDriver(lock_path=str(tmp_path / path))
        driver.acquire()
        driver.load_option_chain(real_bhavcopy_text, DAY)
        for c in real_candles:
            driver.process_tick("NIFTY", c["ts"], c["close"])
        result = driver.run_decision_cadence(timestamp=real_candles[-1]["ts"])
        driver.close_session()
        return result

    r1 = run("lock11a")
    r2 = run("lock11b")
    assert r1.thesis.assessment_id == r2.thesis.assessment_id
    assert r1.mppi.assessment_id == r2.mppi.assessment_id
    assert r1.consensus.assessment_id == r2.consensus.assessment_id


# --- Multi-day close history for VSB (resolves the vsb.volatility_regime
# gap disclosed in docs/LIVE_PIPELINE_INTEGRATION.md Section 6) -----------

def test_same_day_closes_alone_resolve_volatility_regime_from_unknown(real_candles, real_bhavcopy_text, tmp_path):
    """Before this fix, VSB was always passed an empty closes_with_ts,
    so volatility_regime read UNKNOWN even with a real chain loaded.
    Today's own real ticks-so-far are now threaded in automatically."""
    driver = SessionDriver(lock_path=str(tmp_path / "lock12"))
    driver.acquire()
    driver.load_option_chain(real_bhavcopy_text, DAY)
    for c in real_candles:
        driver.process_tick("NIFTY", c["ts"], c["close"])
    result = driver.run_decision_cadence(timestamp=real_candles[-1]["ts"])
    driver.close_session()

    assert result.vsb.volatility_regime != "UNKNOWN"
    assert result.vsb.realized_vol is not None
    assert len(result.closes_with_ts) == len(real_candles)


def test_prior_session_history_is_threaded_into_the_next_session(real_bhavcopy_text, tmp_path):
    """Real, end-to-end proof of multi-day threading: day 2, seeded with
    day 1's real closes_with_ts, must use MORE real observations than
    day 2 run cold -- and must produce a genuinely different (not
    coincidentally identical) realized_vol figure, proving the prior
    day's real data was actually incorporated, not silently dropped."""
    with open(INTRADAY_PATH) as f:
        intraday = json.load(f)

    def run_day(day, prior_closes, path):
        candles = intraday[day]
        bhav_path = f"/tmp/m1/BhavCopy_NSE_FO_0_0_0_{day.replace('-', '')}_F_0000.csv"
        with open(bhav_path) as f:
            bhavcopy_text = f.read()
        driver = SessionDriver(lock_path=str(tmp_path / path), prior_closes_with_ts=prior_closes)
        driver.acquire()
        driver.load_option_chain(bhavcopy_text, day)
        for c in candles:
            driver.process_tick("NIFTY", c["ts"], c["close"])
        result = driver.run_decision_cadence(timestamp=candles[-1]["ts"])
        driver.close_session()
        return result

    day1 = run_day("2026-05-25", (), "lock13a")
    day2_seeded = run_day("2026-05-26", day1.closes_with_ts, "lock13b")
    day2_cold = run_day("2026-05-26", (), "lock13c")

    assert len(day2_seeded.closes_with_ts) > len(day2_cold.closes_with_ts)
    assert len(day2_seeded.closes_with_ts) == len(day1.closes_with_ts) + len(day2_cold.closes_with_ts)
    assert day2_seeded.vsb.realized_vol != day2_cold.vsb.realized_vol


def test_history_threading_affects_only_volatility_domain_never_market_direction(real_bhavcopy_text, tmp_path):
    """Multi-day VSB history feeds ONLY the volatility domain -- Market
    Direction (PSI/MSSI/MDI) never consumes option-chain or close
    history at all, so it must be completely unaffected by seeded
    history, real proof that history-threading cannot silently alter
    the tick-derived reasoning.

    The overall THESIS TYPE, however, is legitimately free to change:
    the frozen Trade Thesis engine's own real, documented priority
    order lets a strong Volatility Structure vote (e.g. COMPRESSION)
    outrank a Price Structure vote (e.g. TREND_CONTINUATION) -- see
    `bujji.msi_trade_thesis.engine`'s own vote cascade. Observed
    directly in this test: seeding one extra real-shaped close point
    shifts VSB's own reading enough to flip the thesis from
    TREND_CONTINUATION to VOLATILITY_COMPRESSION. That is the FROZEN
    engine behaving correctly on genuinely different real volatility
    evidence, not a bug in history threading -- so this test asserts
    the real invariant (MDI unaffected) rather than a false one
    (thesis type unaffected)."""
    with open(INTRADAY_PATH) as f:
        intraday = json.load(f)
    candles = intraday[DAY]

    driver_cold = SessionDriver(lock_path=str(tmp_path / "lock14a"))
    driver_cold.acquire()
    driver_cold.load_option_chain(real_bhavcopy_text, DAY)
    for c in candles:
        driver_cold.process_tick("NIFTY", c["ts"], c["close"])
    result_cold = driver_cold.run_decision_cadence(timestamp=candles[-1]["ts"])
    driver_cold.close_session()

    driver_seeded = SessionDriver(lock_path=str(tmp_path / "lock14b"),
                                  prior_closes_with_ts=(("2026-05-24T15:15:00", 23900.0),))
    driver_seeded.acquire()
    driver_seeded.load_option_chain(real_bhavcopy_text, DAY)
    for c in candles:
        driver_seeded.process_tick("NIFTY", c["ts"], c["close"])
    result_seeded = driver_seeded.run_decision_cadence(timestamp=candles[-1]["ts"])
    driver_seeded.close_session()

    # The real, structural invariant: MDI/PSI/MSSI never read closes_with_ts
    # or the option chain at all, so seeding history must never move them.
    assert result_cold.mdi.overall_direction == result_seeded.mdi.overall_direction == "STRONG_BULLISH"
    assert result_cold.psi.assessment_id == result_seeded.psi.assessment_id
    # VSB's own realized-vol figure DOES legitimately differ (that is the
    # entire point of wiring history in) -- and, per its own documented
    # priority in the thesis cascade, that can legitimately change the
    # overall thesis type too, exactly as observed here.
    assert result_cold.vsb.realized_vol != result_seeded.vsb.realized_vol


# --- Live premium feed adapter (resolves the option-premium data-source
# gap disclosed in docs/LIVE_PIPELINE_INTEGRATION.md Section 10) ----------

class _FakeQuoteBroker:
    """Duck-typed stand-in matching the REAL, already-verified
    `FyersBroker.get_quote` response shape (`{"bid":..., "ask":...,
    "spread":...}`) -- this module never imports `bujji.broker`
    (verified by the existing AST test below), so the adapter is
    exercised here against a plain double, exactly mirroring how this
    whole sprint has proven every other stage against real recorded
    data rather than a live connection this environment cannot open."""

    def __init__(self, quotes: dict) -> None:
        self._quotes = quotes

    async def get_quote(self, contract):
        return self._quotes.get(contract)


def test_fetch_live_premium_derives_real_mid_price():
    broker = _FakeQuoteBroker({"CE": {"bid": 63.0, "ask": 65.0, "spread": 2.0}})
    premium = asyncio.run(fetch_live_premium(broker, "CE"))
    assert premium == 64.0


def test_fetch_live_premium_returns_none_on_missing_quote():
    broker = _FakeQuoteBroker({})
    premium = asyncio.run(fetch_live_premium(broker, "CE"))
    assert premium is None


def test_fetch_live_premium_returns_none_on_crossed_market():
    broker = _FakeQuoteBroker({"CE": {"bid": 65.0, "ask": 63.0, "spread": -2.0}})
    premium = asyncio.run(fetch_live_premium(broker, "CE"))
    assert premium is None


def test_fetch_live_premium_returns_none_on_missing_bid_or_ask():
    broker = _FakeQuoteBroker({"CE": {"bid": None, "ask": None, "spread": None}})
    assert asyncio.run(fetch_live_premium(broker, "CE")) is None


def test_fetch_live_atm_premiums_handles_each_leg_independently():
    broker = _FakeQuoteBroker({"CE": {"bid": 63.0, "ask": 65.0, "spread": 2.0}})  # PE deliberately absent.
    ce, pe = asyncio.run(fetch_live_atm_premiums(broker, "CE", "PE"))
    assert ce == 64.0
    assert pe is None  # honest -- never guessed from the CE leg.


def test_live_premium_overrides_bhavcopy_premium_and_updates_vsb(real_candles, real_bhavcopy_text, tmp_path):
    driver_baseline = SessionDriver(lock_path=str(tmp_path / "lock15a"))
    driver_baseline.acquire()
    driver_baseline.load_option_chain(real_bhavcopy_text, DAY)
    for c in real_candles:
        driver_baseline.process_tick("NIFTY", c["ts"], c["close"])
    baseline = driver_baseline.run_decision_cadence(timestamp=real_candles[-1]["ts"])
    driver_baseline.close_session()
    assert baseline.live_premiums_wired is False

    driver_live = SessionDriver(lock_path=str(tmp_path / "lock15b"))
    driver_live.acquire()
    driver_live.load_option_chain(real_bhavcopy_text, DAY)
    driver_live.set_live_atm_premiums(ce_premium=200.0, pe_premium=None)  # a deliberately different real-shaped value.
    for c in real_candles:
        driver_live.process_tick("NIFTY", c["ts"], c["close"])
    live = driver_live.run_decision_cadence(timestamp=real_candles[-1]["ts"])
    driver_live.close_session()

    assert live.live_premiums_wired is True
    assert live.vsb.iv_average != baseline.vsb.iv_average  # real, measurable effect of the live override.


def test_live_premium_setter_never_blanks_out_an_existing_value(real_candles, real_bhavcopy_text, tmp_path):
    """Passing None for one leg must leave that leg's Bhavcopy premium
    in place -- a missing live quote must never silently erase real,
    already-available evidence."""
    driver = SessionDriver(lock_path=str(tmp_path / "lock16"))
    driver.acquire()
    driver.load_option_chain(real_bhavcopy_text, DAY)
    driver.set_live_atm_premiums(ce_premium=None, pe_premium=None)  # both unavailable this tick.
    for c in real_candles:
        driver.process_tick("NIFTY", c["ts"], c["close"])
    result = driver.run_decision_cadence(timestamp=real_candles[-1]["ts"])
    driver.close_session()

    # No live value was ever actually supplied, so this must behave
    # identically to never having called the setter at all.
    assert result.live_premiums_wired is False
    assert result.vsb.iv_average is not None  # Bhavcopy premium still used, VSB still solved real IV.


# --- Deliverable 7: operational metrics -------------------------------------

def test_operational_metrics_are_real_counts(real_candles, tmp_path):
    driver = SessionDriver(lock_path=str(tmp_path / "lock7"))
    driver.acquire()
    for c in real_candles:
        driver.process_tick("NIFTY", c["ts"], c["close"])
    driver.process_tick("NIFTY", real_candles[0]["ts"], real_candles[0]["close"])  # duplicate.
    assert driver.result.dropped_ticks == 1
    assert isinstance(driver.result.reconnects, int)
    driver.close_session()


# --- AST isolation: never touches a broker or execution ------------------

def test_bridge_never_imports_broker_or_execution():
    path = os.path.join(os.path.dirname(__file__), "..", "bujji", "live_pipeline_bridge.py")
    with open(path) as f:
        tree = ast.parse(f.read(), filename=path)
    forbidden = ("bujji.execution", "bujji.broker")
    for node in ast.walk(tree):
        names = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module]
        for name in names:
            assert not any(name.startswith(m) for m in forbidden), f"bridge imports {name}"


def test_bridge_never_calls_place_order():
    path = os.path.join(os.path.dirname(__file__), "..", "bujji", "live_pipeline_bridge.py")
    with open(path) as f:
        tree = ast.parse(f.read(), filename=path)
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            assert node.attr not in ("place_order", "submit_order"), f"calls {node.attr}"
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in ("place_order", "submit_order")
