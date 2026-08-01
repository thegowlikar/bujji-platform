"""Tests — Numeric Risk Governor Gate C.5 (real broker calibration
runner, READ-ONLY, HUMAN-TRIGGERED). ZERO network access anywhere in
this file -- every test uses a fake, in-memory broker object; no real
Fyers/broker credentials or connections are ever constructed here."""
from __future__ import annotations

import ast
import inspect
from datetime import datetime, timezone
from pathlib import Path

import pytest

from bujji.core.enums import OptionType
from bujji.core.models import OptionContract
from bujji.trading_brain.risk_governor import margin_calibration_runner
from bujji.trading_brain.risk_governor.broker_margin_reality_adapter import FyersMarginProvider
from bujji.trading_brain.risk_governor.margin_calibration_runner import (
    DuplicateCalibrationSampleError,
    MarginCalibrationRunner,
    MarginCalibrationStore,
    STATUS_CAPTURED,
    STATUS_SAMPLE_NOT_CAPTURED,
    build_calibration_run,
    build_certification_result_from_samples,
)
from bujji.trading_brain.risk_governor.margin_certification_engine import (
    CERTIFICATION_CERTIFIED,
    MarginCertificationEngine,
)
from bujji.trading_brain.risk_governor.simulated_margin_provider import SimulatedMarginProvider
from bujji.trading_brain.risk_governor.whole_book_margin_provider import MarginLegRequest


def _clock(iso="2026-08-02T09:15:00+00:00"):
    dt = datetime.fromisoformat(iso)
    return lambda: dt


def _ce():
    return OptionContract(symbol="NSE:NIFTY26AUG24800CE", underlying="NIFTY", strike=24800,
                           option_type=OptionType.CE, expiry="2026-08-27", lot_size=75)


def _pe():
    return OptionContract(symbol="NSE:NIFTY26AUG24800PE", underlying="NIFTY", strike=24800,
                           option_type=OptionType.PE, expiry="2026-08-27", lot_size=75)


def _legs():
    return [
        MarginLegRequest(symbol="NSE:NIFTY26AUG24800CE", qty=75, side=-1, instrument_type="OPTIDX",
                          product_type="MIS", limit_price=90.0),
        MarginLegRequest(symbol="NSE:NIFTY26AUG24800PE", qty=75, side=-1, instrument_type="OPTIDX",
                          product_type="MIS", limit_price=85.0),
    ]


class _FakeBroker:
    """No place_order/cancel_order exist on this object at all."""

    def __init__(self, funds=None, order_margin=None):
        self._funds = funds
        self._order_margin = order_margin

    async def get_funds(self):
        return self._funds

    async def get_order_margin(self, ce_contract, pe_contract):
        return self._order_margin


def _runner(funds=None, order_margin=None, store=None):
    # NOTE: `store or MarginCalibrationStore()` would be a real bug here --
    # MarginCalibrationStore defines __len__, so an EMPTY store (len==0)
    # is falsy in Python, silently discarding the caller's actual store
    # and creating a fresh one. Use an explicit `is None` check instead.
    simulated_provider = SimulatedMarginProvider()
    broker_provider = FyersMarginProvider(_FakeBroker(funds=funds, order_margin=order_margin))
    return MarginCalibrationRunner(simulated_provider, broker_provider, store if store is not None else MarginCalibrationStore())


_HAPPY_FUNDS = {"available_margin": 1_000_000.0, "used_margin": 100_000.0}
# A naked short straddle (both legs SELL, no hedge) via _legs() computes
# to SimulatedMarginProvider's naked-short rate (15x) * (75*90 + 75*85) =
# 196875.0 -- kept close to that here so happy-path tests exercise a
# realistic PASS/CERTIFIED comparison, not an arbitrary mismatched figure.
_HAPPY_ORDER_MARGIN = {"margin_per_lot": 199500.0, "verified": False, "source": "fyers_span_margin"}


# --------------------------------------------------------------------- #
# Runner 1/2 -- successful calibration sample creation, full pipeline
# --------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_successful_calibration_sample_creation():
    store = MarginCalibrationStore()
    runner = _runner(funds=_HAPPY_FUNDS, order_margin=_HAPPY_ORDER_MARGIN, store=store)
    result = await runner.capture_sample(
        "sample-1", "NIFTY_SHORT_STRADDLE", "NIFTY 24800 CE+PE short straddle",
        _legs(), _ce(), _pe(), clock=_clock(),
    )
    assert result.captured is True
    assert result.status == STATUS_CAPTURED
    assert result.sample.sample_id == "sample-1"
    assert result.sample.strategy_type == "NIFTY_SHORT_STRADDLE"
    assert len(store) == 1
    assert store.get("sample-1") is result.sample


@pytest.mark.asyncio
async def test_simulation_and_broker_comparison_pipeline_wired_correctly():
    runner = _runner(funds=_HAPPY_FUNDS, order_margin=_HAPPY_ORDER_MARGIN)
    result = await runner.capture_sample(
        "sample-1", "NIFTY_SHORT_STRADDLE", "desc", _legs(), _ce(), _pe(), clock=_clock(),
    )
    sample = result.sample
    assert sample.simulated_margin_snapshot.margin_verified is True
    assert sample.broker_margin_snapshot.available is True
    assert sample.broker_margin_snapshot.required_margin == 199500.0
    assert sample.comparison_report.simulated_margin == sample.simulated_margin_snapshot.required_margin
    assert sample.comparison_report.broker_margin == 199500.0


# --------------------------------------------------------------------- #
# Runner 3 -- multiple samples in one run
# --------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_multiple_samples_in_one_run():
    store = MarginCalibrationStore()
    runner = _runner(funds=_HAPPY_FUNDS, order_margin=_HAPPY_ORDER_MARGIN, store=store)
    for i in range(5):
        result = await runner.capture_sample(
            f"sample-{i}", "NIFTY_SHORT_STRADDLE", "desc", _legs(), _ce(), _pe(), clock=_clock(),
        )
        assert result.captured is True
    assert len(store) == 5
    assert {s.sample_id for s in store.samples()} == {f"sample-{i}" for i in range(5)}


# --------------------------------------------------------------------- #
# Safety 4 -- broker unavailable
# --------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_broker_unavailable_yields_sample_not_captured_never_zero_margin():
    store = MarginCalibrationStore()
    runner = _runner(funds=None, order_margin=None, store=store)
    result = await runner.capture_sample(
        "sample-1", "NIFTY_SHORT_STRADDLE", "desc", _legs(), _ce(), _pe(), clock=_clock(),
    )
    assert result.captured is False
    assert result.status == STATUS_SAMPLE_NOT_CAPTURED
    assert result.sample is None
    assert len(store) == 0   # nothing written to the store at all


# --------------------------------------------------------------------- #
# Safety 5 -- partial broker response
# --------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_partial_broker_response_rejects_sample():
    store = MarginCalibrationStore()
    runner = _runner(funds=_HAPPY_FUNDS, order_margin=None, store=store)  # funds ok, order_margin missing
    result = await runner.capture_sample(
        "sample-1", "NIFTY_SHORT_STRADDLE", "desc", _legs(), _ce(), _pe(), clock=_clock(),
    )
    assert result.captured is False
    assert len(store) == 0


# --------------------------------------------------------------------- #
# Safety 6 -- duplicate sample ID
# --------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_duplicate_sample_id_rejected_not_overwritten():
    store = MarginCalibrationStore()
    runner = _runner(funds=_HAPPY_FUNDS, order_margin=_HAPPY_ORDER_MARGIN, store=store)
    first = await runner.capture_sample("dup-1", "NIFTY_SHORT_STRADDLE", "desc", _legs(), _ce(), _pe(), clock=_clock())
    assert first.captured is True

    second = await runner.capture_sample(
        "dup-1", "NIFTY_SHORT_STRADDLE", "different description", _legs(), _ce(), _pe(),
        clock=_clock("2026-08-02T10:00:00+00:00"),
    )
    assert second.captured is False
    assert second.status == STATUS_SAMPLE_NOT_CAPTURED
    assert len(store) == 1
    # the ORIGINAL sample's description must be unchanged
    assert store.get("dup-1").position_description == "desc"


def test_store_record_sample_raises_on_direct_duplicate_call():
    store = MarginCalibrationStore()
    runner_provider = SimulatedMarginProvider()
    snapshot = runner_provider.get_portfolio_margin(_legs(), clock=_clock())
    from bujji.trading_brain.risk_governor.margin_calibration_runner import MarginCalibrationSample
    from bujji.trading_brain.risk_governor.margin_comparison_engine import compare_margin
    from bujji.trading_brain.risk_governor.broker_margin_reality_adapter import BrokerMarginSnapshot

    broker_snapshot = BrokerMarginSnapshot(available_margin=1000000.0, used_margin=0.0, required_margin=199500.0,
                                            timestamp=_clock()(), source="FYERS_READ_ONLY", available=True)
    report = compare_margin(snapshot, broker_snapshot, clock=_clock())
    sample = MarginCalibrationSample(
        sample_id="x", timestamp=_clock()(), strategy_type="STRADDLE", position_description="d",
        margin_leg_input=tuple(_legs()), simulated_margin_snapshot=snapshot,
        broker_margin_snapshot=broker_snapshot, comparison_report=report,
    )
    store.record_sample(sample)
    with pytest.raises(DuplicateCalibrationSampleError):
        store.record_sample(sample)


# --------------------------------------------------------------------- #
# Safety 7 -- no mutation of historical samples
# --------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_no_mutation_of_historical_samples():
    store = MarginCalibrationStore()
    runner = _runner(funds=_HAPPY_FUNDS, order_margin=_HAPPY_ORDER_MARGIN, store=store)
    result = await runner.capture_sample("s1", "STRADDLE", "original desc", _legs(), _ce(), _pe(), clock=_clock())
    original_sample = result.sample
    original_margin = original_sample.simulated_margin_snapshot.required_margin

    # capture more samples, export, read repeatedly -- none of this may alter s1
    for i in range(3):
        await runner.capture_sample(f"s{i+2}", "STRADDLE", "desc", _legs(), _ce(), _pe(), clock=_clock())
    store.export_samples()
    retrieved_again = store.get("s1")

    assert retrieved_again is original_sample   # same object, never replaced
    assert retrieved_again.simulated_margin_snapshot.required_margin == original_margin
    assert retrieved_again.position_description == "original desc"
    assert store.samples()[0] is original_sample


def test_store_has_no_update_or_delete_method():
    store = MarginCalibrationStore()
    assert not hasattr(store, "update_sample")
    assert not hasattr(store, "delete_sample")
    assert not hasattr(store, "clear")


# --------------------------------------------------------------------- #
# Safety 8 -- no order methods accessible
# --------------------------------------------------------------------- #

def test_fake_broker_has_no_mutating_methods():
    broker = _FakeBroker()
    assert not hasattr(broker, "place_order")
    assert not hasattr(broker, "cancel_order")


def test_source_never_references_mutating_broker_methods():
    source_path = Path(inspect.getfile(margin_calibration_runner))
    tree = ast.parse(source_path.read_text())
    forbidden_names = {"place_order", "cancel_order", "modify_order", "get_open_positions"}
    referenced_attrs = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    referenced_names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    offenders = forbidden_names & (referenced_attrs | referenced_names)
    assert offenders == set(), f"forbidden mutating method references found: {offenders}"


def test_no_live_broker_or_execution_imports():
    source_path = Path(inspect.getfile(margin_calibration_runner))
    tree = ast.parse(source_path.read_text())
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    forbidden_prefixes = ("bujji.broker.fyers", "bujji.broker.hybrid", "bujji.runtime_execution")
    offenders = [m for m in imported if any(m == p or m.startswith(p + ".") for p in forbidden_prefixes)]
    assert offenders == [], f"forbidden imports found: {offenders}"


def test_no_background_execution_primitives_anywhere():
    """No scheduler, no daemon, no background thread, no automatic
    polling -- AST-verified, not a substring scan."""
    source_path = Path(inspect.getfile(margin_calibration_runner))
    tree = ast.parse(source_path.read_text())
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    forbidden_modules = ("threading", "sched", "multiprocessing", "concurrent.futures")
    offenders = [m for m in imported if m in forbidden_modules]
    assert offenders == [], f"forbidden background-execution imports found: {offenders}"

    forbidden_calls = {"create_task", "ensure_future", "Timer", "Thread", "run_forever"}
    referenced_attrs = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    offenders_calls = forbidden_calls & referenced_attrs
    assert offenders_calls == set(), f"forbidden background-execution calls found: {offenders_calls}"


@pytest.mark.asyncio
async def test_missing_strategy_type_rejected_not_silently_defaulted():
    """Step 7: missing strategy metadata -> reject (this session's
    chosen resolution: reject with an explicit instruction to pass
    'UNKNOWN', never silently default to it)."""
    runner = _runner(funds=_HAPPY_FUNDS, order_margin=_HAPPY_ORDER_MARGIN)
    result = await runner.capture_sample("s1", "", "desc", _legs(), _ce(), _pe(), clock=_clock())
    assert result.captured is False
    assert "strategy_type" in result.reason


# --------------------------------------------------------------------- #
# Certification integration 9/10
# --------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_calibration_samples_correctly_feed_certification_engine():
    store = MarginCalibrationStore()
    runner = _runner(funds=_HAPPY_FUNDS, order_margin=_HAPPY_ORDER_MARGIN, store=store)
    for i in range(5):
        await runner.capture_sample(f"s{i}", "NIFTY_SHORT_STRADDLE", "desc", _legs(), _ce(), _pe(), clock=_clock())

    run = build_calibration_run("run-1", store, clock=_clock())
    assert len(run.samples) == 5
    assert run.certification_result is not None
    assert run.certification_result.total_cases == 5


@pytest.mark.asyncio
async def test_certification_result_matches_direct_engine_invocation():
    store = MarginCalibrationStore()
    runner = _runner(funds=_HAPPY_FUNDS, order_margin=_HAPPY_ORDER_MARGIN, store=store)
    for i in range(3):
        await runner.capture_sample(f"s{i}", "STRADDLE", "desc", _legs(), _ce(), _pe(), clock=_clock())

    engine = MarginCertificationEngine()
    run = build_calibration_run("run-1", store, clock=_clock(), engine=engine)

    direct_result = build_certification_result_from_samples(list(store.samples()), engine)
    assert run.certification_result.certification_status == direct_result.certification_status
    assert run.certification_result.average_deviation == direct_result.average_deviation
    assert run.certification_result.certification_status == CERTIFICATION_CERTIFIED


@pytest.mark.asyncio
async def test_empty_store_certification_is_insufficient_data_not_error():
    store = MarginCalibrationStore()
    run = build_calibration_run("run-empty", store, clock=_clock())
    assert run.certification_result.certification_status == "INSUFFICIENT_DATA"
    assert run.samples == ()
