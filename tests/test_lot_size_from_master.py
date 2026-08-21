"""Lot size must come from the instrument master, never a config constant.

Fixtures write real 21-column FYERS symbol-master rows (layout verified live
2026-07-19, documented in instrument_master.py). Assertions are structural:
the ConflictingLotSizeError carries its breakdown as data, not prose.
"""
from __future__ import annotations

import importlib.util
import logging
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from bujji.broker.instrument_master import (  # noqa: E402
    ConflictingLotSizeError, InstrumentMaster,
)

_spec = importlib.util.spec_from_file_location(
    "bujji_options_os_runner", REPO_ROOT / "bujji_options_os_runner.py")
runner = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(runner)

LOG = logging.getLogger("test")


def _row(underlying, lot, epoch, symbol, strike, opt_type):
    """One real-layout master row: index 3=lot, 8=epoch, 9=symbol,
    13=underlying, 15=strike, 16=type, 21 columns total."""
    r = [""] * 21
    r[0] = "101126082558067"
    r[1] = f"{underlying} test row"
    r[3] = str(lot)
    r[8] = str(epoch)
    r[9] = symbol
    r[13] = underlying
    r[15] = str(strike)
    r[16] = opt_type
    return ",".join(r)


AUG = 1787652600   # 2026-08-25 epoch, real value from the live master
SEP = 1790676600


def _master(tmp_path, lines):
    (tmp_path / "fyers_fo_NSE.csv").write_text("\n".join(lines) + "\n")
    return InstrumentMaster(tmp_path, LOG)


def test_unanimous_options_and_futures_resolve(tmp_path):
    m = _master(tmp_path, [
        _row("NIFTY", 65, AUG, "NSE:NIFTY26AUG24400CE", 24400.0, "CE"),
        _row("NIFTY", 65, AUG, "NSE:NIFTY26AUG24400PE", 24400.0, "PE"),
        _row("NIFTY", 65, AUG, "NSE:NIFTY26AUGFUT", -1.0, "XX"),
    ])
    assert m.lot_size_for("NIFTY") == 65


def test_transitioning_lot_sizes_fail_closed_with_breakdown(tmp_path):
    m = _master(tmp_path, [
        _row("NIFTY", 65, AUG, "NSE:NIFTY26AUG24400CE", 24400.0, "CE"),
        _row("NIFTY", 75, SEP, "NSE:NIFTY26SEP24400CE", 24400.0, "CE"),
    ])
    with pytest.raises(ConflictingLotSizeError) as exc:
        m.lot_size_for("NIFTY")
    assert exc.value.underlying == "NIFTY"
    assert {s for v in exc.value.by_expiry.values() for s in v} == {65, 75}


def test_futures_disagreeing_with_options_is_also_a_conflict(tmp_path):
    m = _master(tmp_path, [
        _row("NIFTY", 65, AUG, "NSE:NIFTY26AUG24400CE", 24400.0, "CE"),
        _row("NIFTY", 70, AUG, "NSE:NIFTY26AUGFUT", -1.0, "XX"),
    ])
    with pytest.raises(ConflictingLotSizeError):
        m.lot_size_for("NIFTY")


def test_unknown_underlying_raises_lookup(tmp_path):
    m = _master(tmp_path, [
        _row("BANKNIFTY", 30, AUG, "NSE:BANKNIFTY26AUGFUT", -1.0, "XX"),
    ])
    with pytest.raises(LookupError):
        m.lot_size_for("NIFTY")


def test_missing_cache_fails_rather_than_guessing(tmp_path):
    m = InstrumentMaster(tmp_path, LOG)  # dir exists, no CSV inside
    with pytest.raises(FileNotFoundError):
        m.lot_size_for("NIFTY")


def test_other_underlyings_do_not_pollute_the_answer(tmp_path):
    m = _master(tmp_path, [
        _row("NIFTY", 65, AUG, "NSE:NIFTY26AUG24400CE", 24400.0, "CE"),
        _row("BANKNIFTY", 30, AUG, "NSE:BANKNIFTY26AUG52000CE", 52000.0, "CE"),
    ])
    assert m.lot_size_for("NIFTY") == 65
    assert m.lot_size_for("BANKNIFTY") == 30


# ------------------------------------------------- runner resolution


def test_runner_uses_master_over_disagreeing_yaml(tmp_path, caplog):
    _master(tmp_path, [_row("NIFTY", 65, AUG, "NSE:NIFTY26AUG24400CE", 24400.0, "CE")])
    with caplog.at_level(logging.WARNING):
        got = runner._resolve_exchange_lot_size(
            {"underlying": "NIFTY", "exchange_lot_size": 75},
            log=LOG, cache_dir=tmp_path)
    assert got == 65
    assert any(r.levelno == logging.WARNING for r in caplog.records)


def test_runner_agreeing_yaml_produces_no_warning(tmp_path, caplog):
    _master(tmp_path, [_row("NIFTY", 65, AUG, "NSE:NIFTY26AUG24400CE", 24400.0, "CE")])
    with caplog.at_level(logging.WARNING):
        got = runner._resolve_exchange_lot_size(
            {"underlying": "NIFTY", "exchange_lot_size": 65},
            log=LOG, cache_dir=tmp_path)
    assert got == 65
    assert not any(r.levelno == logging.WARNING for r in caplog.records)


def test_runner_absent_yaml_still_resolves(tmp_path):
    _master(tmp_path, [_row("NIFTY", 65, AUG, "NSE:NIFTY26AUG24400CE", 24400.0, "CE")])
    assert runner._resolve_exchange_lot_size(
        {"underlying": "NIFTY"}, log=LOG, cache_dir=tmp_path) == 65


def test_runner_fails_closed_when_master_cannot_answer(tmp_path):
    with pytest.raises(RuntimeError):
        runner._resolve_exchange_lot_size(
            {"underlying": "NIFTY", "exchange_lot_size": 75},
            log=LOG, cache_dir=tmp_path)  # empty dir: no CSV


def test_runner_fails_closed_on_a_lot_size_transition(tmp_path):
    _master(tmp_path, [
        _row("NIFTY", 65, AUG, "NSE:NIFTY26AUG24400CE", 24400.0, "CE"),
        _row("NIFTY", 75, SEP, "NSE:NIFTY26SEP24400CE", 24400.0, "CE"),
    ])
    with pytest.raises(RuntimeError):
        runner._resolve_exchange_lot_size(
            {"underlying": "NIFTY"}, log=LOG, cache_dir=tmp_path)


# ------------------------------------------ production_runtime composition


def _runtime_config(**kw):
    from bujji.production_runtime.config import RuntimeConfig
    return RuntimeConfig(**kw)


def test_composition_root_resolves_lot_from_master(tmp_path):
    from bujji.production_runtime.composition_root import build_composition_root
    _master(tmp_path, [_row("NIFTY", 65, AUG, "NSE:NIFTY26AUG24400CE", 24400.0, "CE")])
    root = build_composition_root(
        _runtime_config(instrument_master_directory=str(tmp_path)), LOG)
    assert root.lot_spec.lot_size == 65


def test_composition_root_master_beats_declared_cross_check(tmp_path, caplog):
    from bujji.production_runtime.composition_root import build_composition_root
    _master(tmp_path, [_row("NIFTY", 65, AUG, "NSE:NIFTY26AUG24400CE", 24400.0, "CE")])
    with caplog.at_level(logging.WARNING):
        root = build_composition_root(
            _runtime_config(lot_size=75, instrument_master_directory=str(tmp_path)), LOG)
    assert root.lot_spec.lot_size == 65
    assert any(r.levelno == logging.WARNING for r in caplog.records)


def test_composition_root_fails_closed_without_a_master(tmp_path):
    from bujji.production_runtime.composition_root import (
        CompositionError, build_composition_root,
    )
    with pytest.raises(CompositionError):
        build_composition_root(
            _runtime_config(instrument_master_directory=str(tmp_path)), LOG)


def test_runtime_config_rejects_nonpositive_cross_check():
    from bujji.production_runtime.config import InvalidRuntimeConfig
    with pytest.raises(InvalidRuntimeConfig):
        _runtime_config(lot_size=0)


def test_runtime_config_carries_no_exchange_default():
    import dataclasses
    from bujji.production_runtime.config import RuntimeConfig
    field = {f.name: f for f in dataclasses.fields(RuntimeConfig)}["lot_size"]
    assert field.default is None, (
        "lot_size must not default to an exchange value -- that is the exact "
        "defect the 2026-07-19 audit documented (master said 65, default said 75)")
