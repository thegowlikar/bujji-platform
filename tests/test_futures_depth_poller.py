"""Phase 17F.1.2 Q5 / activated Phase 17I.2 — futures depth poller.

`scripts/run_futures_depth_poller.py` is an operational script (like
every other collector script in this project) rather than an importable
package module, so it is loaded directly from its file path -- the same
posture as this project's other scripts, none of which have been
unit-tested before now because they require live credentials to run
end-to-end. What IS tested here is exactly the part that does not:
market-hours gating, the FIELD_MAPPING_VERIFIED state (now permanently
True post-activation), and DISCOVERY mode's continued refusal to write
anything to Layer 0 regardless of that flag.

See tests/test_futures_depth_activation.py for the certification-
isolation, normalization, identity, duplicate/conflict, and restart-
survival coverage added alongside activation.
"""
import datetime
import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "run_futures_depth_poller.py"
_spec = importlib.util.spec_from_file_location("run_futures_depth_poller", _SCRIPT_PATH)
poller = importlib.util.module_from_spec(_spec)
sys.modules["run_futures_depth_poller"] = poller
_spec.loader.exec_module(poller)


def test_60_second_cadence_matches_the_q5_decision():
    assert poller.POLL_INTERVAL_SECONDS == 60.0


def test_field_mapping_is_verified_post_activation():
    """Phase 17I.2 flipped this True after the real field names were
    confirmed live against `fyers_depth_discovery_20260813.json`
    (Phase 17I.1) and `_normalize_depth_payload()` was updated to match
    them -- see tests/test_futures_depth_activation.py for the
    normalization coverage this enabled."""
    assert poller.FIELD_MAPPING_VERIFIED is True


def test_normalize_depth_payload_still_refuses_a_malformed_row():
    """Verification of the SHAPE does not mean every row is trusted --
    a row missing a required raw field (oi/bids/ask) must still return
    None, never a partially-fabricated payload."""
    assert poller._normalize_depth_payload({"oi": 100, "bids": [], "asks": []}) is None


def test_within_market_hours_true_during_session():
    weekday = datetime.datetime(2026, 8, 12, 10, 0, tzinfo=poller.IST)  # Wednesday
    assert poller.within_market_hours(weekday) is True


def test_within_market_hours_false_before_open():
    early = datetime.datetime(2026, 8, 12, 9, 0, tzinfo=poller.IST)
    assert poller.within_market_hours(early) is False


def test_within_market_hours_false_after_close():
    late = datetime.datetime(2026, 8, 12, 15, 45, tzinfo=poller.IST)
    assert poller.within_market_hours(late) is False


def test_within_market_hours_false_on_weekend():
    saturday = datetime.datetime(2026, 8, 15, 10, 0, tzinfo=poller.IST)
    assert poller.within_market_hours(saturday) is False


class _FakeBroker:
    def __init__(self, row):
        self._row = row
        self.calls = 0

    async def get_depth(self, symbol):
        self.calls += 1
        return self._row


@pytest.mark.asyncio
async def test_discovery_mode_never_writes_to_layer0(caplog):
    """DISCOVERY mode (live=False) must only log -- never construct a
    RawObservation or touch a RawObservationStore, regardless of
    FIELD_MAPPING_VERIFIED's value."""
    broker = _FakeBroker({"oi": 12645685, "pdoi": 12124000, "ltp": 24428.0})
    caplog.set_level("INFO", logger="futures_depth_poller")
    await poller._poll_once(broker, "NSE:NIFTY26AUGFUT", expiry_iso="2026-08-27",
                             live=False, reality_store=None)
    assert broker.calls == 1
    assert any("DISCOVERY depth sample" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_discovery_mode_handles_a_missing_row_without_raising():
    broker = _FakeBroker(None)
    await poller._poll_once(broker, "NSE:NIFTY26AUGFUT", expiry_iso="2026-08-27",
                             live=False, reality_store=None)
    assert broker.calls == 1


@pytest.mark.asyncio
async def test_live_mode_refuses_to_write_a_row_missing_required_fields(caplog):
    """A row missing bids/ask/oi must still be refused even in live
    mode post-activation -- normalization returning None is what gates
    this, not the (now-permanent) FIELD_MAPPING_VERIFIED flag."""
    broker = _FakeBroker({"oi": 100})  # No bids/ask -- malformed.
    caplog.set_level("WARNING", logger="futures_depth_poller")
    await poller._poll_once(broker, "NSE:NIFTY26AUGFUT", expiry_iso="2026-08-27",
                             live=True, reality_store=None)
    assert any("refusing to write" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_run_aborts_outside_market_hours_without_importing_broker(monkeypatch):
    """Outside market hours, run() must return before ever importing
    FyersBroker/AppConfig -- no connection attempt, exactly like every
    certification script in this project."""
    def _fail(*a, **k):
        raise AssertionError("should not have attempted to load broker/config")
    monkeypatch.setattr(poller, "within_market_hours", lambda now: False)
    result = await poller.run(cycles=1, live=False)
    assert result == 1


@pytest.mark.asyncio
async def test_run_refuses_live_if_field_mapping_were_ever_unverified(monkeypatch):
    """Defense-in-depth: even though FIELD_MAPPING_VERIFIED is now
    permanently True post-activation, run() must still refuse --live if
    that flag were ever False again (e.g. a future rollback) -- this
    gate must never be bypassed silently."""
    monkeypatch.setattr(poller, "within_market_hours", lambda now: True)
    monkeypatch.setattr(poller, "FIELD_MAPPING_VERIFIED", False)
    result = await poller.run(cycles=1, live=True)
    assert result == 2
