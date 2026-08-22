"""A stale instrument master must not size an order.

THE GAP. `InstrumentMaster.lot_size_for()` is cache-only and synchronous BY
DESIGN -- its docstring says the trading startup path "must not grow a network
dependency" -- and it delegates refreshing to "the capture path's
`_ensure_fresh()`", which lives in a DIFFERENT systemd unit. Nothing ever
checked that the other unit had run. If the capture path failed for a week,
the trading session read a week-old master and said nothing at all.

WHY IT MATTERS MORE THAN MOST STALENESS. Lot size is the multiplier on EVERY
order. This harm has already been paid once here: the 2026-07-19 audit found
the session YAML saying 75 while the live master said 65, and every
constructed quantity was 15.4% oversized. A stale master reintroduces exactly
that across an exchange lot-size revision -- which is precisely the moment the
number changes.

THE WINDOW is 96h, not 24h, because the refresher runs on WEEKDAYS: a Monday
session legitimately reads a master last written Friday morning, ~72h earlier.
96h covers that plus one holiday. Past it the session refuses to size orders
rather than sizing from a number nobody has confirmed.
"""
from __future__ import annotations

import importlib.util
import logging
import os
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

LOG = logging.getLogger("test-lotsize-freshness")
AUG = 1787652600


def _runner_module():
    spec = importlib.util.spec_from_file_location(
        "_runner_lotsize_guard", REPO_ROOT / "bujji_options_os_runner.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _row(underlying, lot, epoch, symbol, strike, opt_type):
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


def _master(tmp_path: Path, *, age_hours: float = 0.0) -> Path:
    path = tmp_path / "fyers_fo_NSE.csv"
    path.write_text("\n".join([
        _row("NIFTY", 65, AUG, "NSE:NIFTY26AUG24400CE", 24400.0, "CE"),
        _row("NIFTY", 65, AUG, "NSE:NIFTY26AUG24400PE", 24400.0, "PE"),
    ]) + "\n")
    if age_hours:
        old = time.time() - age_hours * 3600.0
        os.utime(path, (old, old))
    return path


def _cfg(tmp_path, **kw):
    base = {"underlying": "NIFTY", "instrument_master_dir": str(tmp_path)}
    base.update(kw)
    return base


class TestAFreshMasterSizesNormally:
    def test_a_master_written_now_resolves(self, tmp_path):
        _master(tmp_path)
        mod = _runner_module()
        assert mod._resolve_exchange_lot_size(_cfg(tmp_path), LOG) == 65

    def test_a_master_inside_the_window_resolves(self, tmp_path):
        """A Monday session reading Friday's master, ~72h old."""
        _master(tmp_path, age_hours=72)
        mod = _runner_module()
        assert mod._resolve_exchange_lot_size(_cfg(tmp_path), LOG) == 65

    def test_the_age_is_always_logged(self, tmp_path, caplog):
        _master(tmp_path, age_hours=10)
        mod = _runner_module()
        with caplog.at_level(logging.INFO):
            mod._resolve_exchange_lot_size(_cfg(tmp_path), LOG)
        assert any("INSTRUMENT MASTER" in r.getMessage() for r in caplog.records), (
            "the age must be visible on every session, not only when it fails "
            "-- drift is what you want to see BEFORE it crosses the limit")


class TestAStaleMasterBlocksSizing:
    def test_past_the_window_it_refuses(self, tmp_path):
        _master(tmp_path, age_hours=97)
        mod = _runner_module()
        with pytest.raises(RuntimeError) as exc:
            mod._resolve_exchange_lot_size(_cfg(tmp_path), LOG)
        message = str(exc.value)
        assert "past the 96h limit" in message
        assert "refusing to size orders" in message

    def test_the_message_says_how_to_resolve_it(self, tmp_path):
        _master(tmp_path, age_hours=200)
        mod = _runner_module()
        with pytest.raises(RuntimeError) as exc:
            mod._resolve_exchange_lot_size(_cfg(tmp_path), LOG)
        assert "Refresh the master" in str(exc.value)

    def test_the_window_is_configurable(self, tmp_path):
        _master(tmp_path, age_hours=30)
        mod = _runner_module()
        # Default 96h admits it; an operator tightening to 24h does not.
        assert mod._resolve_exchange_lot_size(_cfg(tmp_path), LOG) == 65
        with pytest.raises(RuntimeError):
            mod._resolve_exchange_lot_size(
                _cfg(tmp_path, instrument_master_max_age_hours=24), LOG)


class TestTheOlderFailClosedBehaviourSurvives:
    def test_a_missing_master_still_refuses(self, tmp_path):
        """No file at all must keep failing closed -- the freshness check must
        not accidentally become the ONLY check."""
        mod = _runner_module()
        with pytest.raises(RuntimeError) as exc:
            mod._resolve_exchange_lot_size(_cfg(tmp_path), LOG)
        assert "unresolvable from the" in str(exc.value)

    def test_an_empty_master_still_refuses(self, tmp_path):
        (tmp_path / "fyers_fo_NSE.csv").write_text("")
        mod = _runner_module()
        with pytest.raises(RuntimeError):
            mod._resolve_exchange_lot_size(_cfg(tmp_path), LOG)
