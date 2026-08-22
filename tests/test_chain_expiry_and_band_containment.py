"""The chain's expiry stops being a subscript, and the band stops fitting by luck.

Two independent assumptions are removed here.

`expiryData[0]` was stamped on EVERY row on the stated grounds that an
unspecified timestamp returns the nearest expiry. That hid two unverified
beliefs -- that FYERS sorts `expiryData`, and that the rows belong to whichever
entry sits first. If either is ever wrong the whole book is mislabelled at once,
and `select_expiry` then "chooses" an expiry whose contracts are somebody
else's. The operator's brief forbids `expiryData[0]` on the trading path.

Separately, the eligible band (bounded by `strike_count`) fitting inside the
subscribed universe (bounded by the tier table) was an accident of two
independently configured numbers.
"""
from __future__ import annotations

import ast
import io
import json
import os
from pathlib import Path

import pytest

from bujji.production_runtime.live_chain_provider import LiveChainProvider, _to_iso_expiry

CAPTURE = Path("/opt/bujji/app/data_certification/fyers_option_chain_discovery_20260813.json")
_HAVE = CAPTURE.exists()
AS_OF = "2026-08-13"


def _universe_admitting(*symbols, selection_band_points=1000, atm=24000):
    """A REAL CaptureUniverse admitting exactly `symbols`."""
    from bujji.capture_universe.builder import (
        CaptureInstrument, CaptureUniverse, KIND_OPTION, ROLE_FRONT)
    return CaptureUniverse(
        as_of_date=AS_OF, spot=float(atm), atm_strike=atm,
        instruments=tuple(
            CaptureInstrument(symbol=x, kind=KIND_OPTION, role=ROLE_FRONT,
                              expiry="2026-08-18", strike=float(atm), option_type="CE")
            for x in symbols),
        roles_resolved={ROLE_FRONT: "2026-08-18"}, collapsed_roles=(),
        expiries_available=1, expiries_excluded=0,
        selection_band_points=selection_band_points)


def _provider(resolver=None, admits=None):
    return LiveChainProvider(
        object(), underlying="NIFTY", expiry_resolver=resolver,
        universe_source=lambda: _universe_admitting(*(admits or [])))


def _raw(expiry_entries, rows):
    return {"data": {"expiryData": expiry_entries, "optionsChain": rows}}


def _row(strike, opt, symbol, ltp=100.0):
    return {"strike_price": strike, "option_type": opt, "symbol": symbol, "ltp": ltp,
            "oi": 1000, "volume": 10, "oich": 0}


# ------------------------------------------------- the expiry is not position 0
def test_the_nearest_expiry_is_computed_not_taken_by_position():
    """`expiryData` arriving unsorted must not mislabel the entire book."""
    raw = _raw(
        [{"date": "25-08-2026"}, {"date": "18-08-2026"}, {"date": "01-09-2026"}],
        [_row(24000.0, "CE", "NSE:NIFTY2681824000CE")],
    )
    syms = [r["symbol"] for r in raw["data"]["optionsChain"]]
    chain, _spot = _provider(admits=syms)._build(raw, AS_OF, admissible=set(syms))
    assert chain[0].observation.identity.timestamp  # sanity: a real observation
    assert _expiry_of(chain[0]) == "2026-08-18", "position 0 was 25-08, the nearest is 18-08"


def test_unparseable_expiry_entries_are_skipped_not_trusted():
    raw = _raw([{"date": "garbage"}, {"date": "18-08-2026"}],
               [_row(24000.0, "CE", "NSE:NIFTY2681824000CE")])
    syms = [r["symbol"] for r in raw["data"]["optionsChain"]]
    chain, _ = _provider(admits=syms)._build(raw, AS_OF, admissible=set(syms))
    assert _expiry_of(chain[0]) == "2026-08-18"


def _expiry_of(observation):
    # OptionObservation stores expiry inside its own identity/value; read it
    # back the way the construction engine does.
    return getattr(observation, "expiry", None)


# ------------------------------------------- per-row expiry from the master
def test_the_resolver_stamps_each_row_with_its_own_real_expiry():
    """One uniform stamp cannot be right for a chain that spans expiries."""
    truth = {"NSE:NIFTY2681824000CE": "2026-08-18",
             "NSE:NIFTY2682524000PE": "2026-08-25"}
    raw = _raw([{"date": "18-08-2026"}],
               [_row(24000.0, "CE", "NSE:NIFTY2681824000CE"),
                _row(24000.0, "PE", "NSE:NIFTY2682524000PE")])
    syms = [r["symbol"] for r in raw["data"]["optionsChain"]]
    chain, _ = _provider(resolver=truth.get, admits=syms)._build(raw, AS_OF, admissible=set(syms))
    got = sorted(_expiry_of(c) for c in chain)
    assert got == ["2026-08-18", "2026-08-25"], got


def test_a_symbol_the_master_does_not_list_is_dropped_not_borrowed():
    """Stamping a borrowed expiry on an unknown contract is the same class of
    defect as fabricating its symbol: a made-up value in a field whose whole
    purpose is to be authoritative."""
    raw = _raw([{"date": "18-08-2026"}],
               [_row(24000.0, "CE", "NSE:NIFTY2681824000CE"),
                _row(24000.0, "PE", "NSE:NIFTYUNKNOWNPE")])
    resolver = {"NSE:NIFTY2681824000CE": "2026-08-18"}.get
    syms = [r["symbol"] for r in raw["data"]["optionsChain"]]
    chain, _ = _provider(resolver=resolver, admits=syms)._build(raw, AS_OF, admissible=set(syms))
    assert len(chain) == 1
    assert chain[0].instrument_symbol == "NSE:NIFTY2681824000CE"


def test_without_a_resolver_the_uniform_stamp_still_applies():
    """The resolver is additive. Callers that wire none keep prior behaviour
    rather than silently losing their chain."""
    raw = _raw([{"date": "18-08-2026"}], [_row(24000.0, "CE", "NSE:NIFTY2681824000CE")])
    syms = [r["symbol"] for r in raw["data"]["optionsChain"]]
    chain, _ = _provider(admits=syms)._build(raw, AS_OF, admissible=set(syms))
    assert len(chain) == 1 and _expiry_of(chain[0]) == "2026-08-18"


# ------------------------------------------------------ the real capture
@pytest.mark.skipif(not _HAVE, reason="dated FYERS capture not present")
class TestAgainstTheRealCapture:
    def _data(self):
        return json.loads(CAPTURE.read_text())["raw_response"]["data"]

    def test_the_capture_carries_eighteen_expiries_unsorted_risk_is_real(self):
        entries = self._data()["expiryData"]
        assert len(entries) == 18
        dates = [_to_iso_expiry(e["date"]) for e in entries]
        assert all(dates), dates
        assert min(dates) == dates[0], (
            "this capture happens to be sorted -- which is exactly why the old "
            "subscript looked correct; the code no longer depends on it")

    def test_each_entry_carries_an_epoch_and_a_weekly_monthly_flag(self):
        """Documents the request-side lever for choosing an expiry deliberately
        (FyersBroker.get_option_chain_raw passes timestamp=""). Requesting a
        specific expiry cannot be verified without a live call, so it is not
        done here -- but the fields it needs are real and are pinned."""
        entry = self._data()["expiryData"][0]
        assert set(("date", "expiry", "expiry_flag")) <= set(entry)
        assert entry["expiry"].isdigit(), entry
        assert entry["expiry_flag"] in ("W", "M"), entry


# --------------------------------------------- containment by construction
RUNNER_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "bujji_options_os_runner.py")
RUNNER_SRC = io.open(RUNNER_PATH, encoding="utf-8").read()


def test_the_chain_width_derives_from_the_selection_band():
    """REPLACES the tier-derivation tests. Those asserted the opposite
    authority: capture TIERS derived from a `strike_count` constant, so a
    chain-request number decided how much of the book was subscribed. The
    universe now states its own selection band and the request derives from
    THAT."""
    from bujji.capture_universe.builder import selection_strikes_each_side
    for points, expected in ((1000, 20), (1500, 30), (500, 10)):
        u = _universe_admitting(selection_band_points=points)
        assert selection_strikes_each_side(u, step=50) == expected


def test_the_provider_requests_exactly_the_selection_width():
    """The number that reaches the broker is the universe's, not a config's."""
    import ast
    src = io.open(os.path.join(os.path.dirname(RUNNER_PATH), "bujji",
                               "production_runtime", "live_chain_provider.py"),
                  encoding="utf-8").read()
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.FunctionDef) and n.name == "_fetch")
    body = ast.unparse(fn)
    assert "selection_strikes_each_side(universe)" in body
    assert "strike_count=strike_count" in body
    assert "self._strike_count" not in body, "a configured width still reaches the request"


def test_a_selection_band_wider_than_capture_is_refused_at_build_time():
    """The containment invariant, ENFORCED rather than assumed. Without it the
    failure is invisible: a strategy could select a contract that was never
    subscribed, so stage 1 would refuse every cycle for a reason no one could
    trace to its cause."""
    import datetime, logging
    from pathlib import Path
    from bujji.broker.instrument_master import InstrumentMaster
    from bujji.capture_universe.builder import (
        build_capture_universe, DEFAULT_TIERS, ROLE_FRONT, UniverseConstructionError)

    master_dir = Path("/opt/bujji/app/data/instrument_master")
    if not (master_dir / "fyers_fo_NSE.csv").exists():
        pytest.skip("instrument master cache not present")
    rows = InstrumentMaster(master_dir, logging.getLogger("t"))._rows_for("NIFTY")
    too_wide = DEFAULT_TIERS[ROLE_FRONT] + 500
    with pytest.raises(UniverseConstructionError, match="exceeds the FRONT capture tier"):
        build_capture_universe(rows, 24216.65, datetime.date(2026, 8, 24),
                               selection_band_points=too_wide)


def test_the_obsolete_strike_count_key_is_refused_not_ignored():
    """No compatibility default. A config still carrying the key that USED to
    be the selection band must fail to start, naming its replacement."""
    src = io.open(RUNNER_PATH, encoding="utf-8").read()
    assert '"strike_count" in market_data_cfg' in src
    assert "selection_band_points" in src
    assert 'market_data_cfg.get("strike_count", 20)' not in src, \
        "the obsolete key is still readable as a value"


def test_the_expiry_resolver_is_actually_wired_into_the_live_provider():
    """BUILT-NOT-WIRED IS THIS CODEBASE'S DOMINANT DEFECT CLASS. A resolver
    the runner never passes is a resolver that does nothing, and every test
    above would still pass."""
    tree = ast.parse(RUNNER_SRC)
    constructions = [n for n in ast.walk(tree)
                     if isinstance(n, ast.Call)
                     and getattr(n.func, "id", None) == "LiveChainProvider"]
    assert constructions, "the runner never constructs LiveChainProvider"
    for call in constructions:
        kwargs = {k.arg for k in call.keywords}
        assert "expiry_resolver" in kwargs, (
            "LiveChainProvider is built without an expiry_resolver -- the chain "
            "would fall back to one stamped expiry for every row")


def test_the_resolver_fails_closed_when_the_master_cannot_be_read():
    """Returning the uniform stamp on a master failure would trade on exactly
    the unverified expiry this change removes."""
    from unittest.mock import MagicMock
    import bujji_options_os_runner as runner

    r = object.__new__(runner.OptionsOSRunner)
    r._logger = MagicMock()
    r._session_cfg = {"underlying": "NOT_A_REAL_UNDERLYING"}
    resolve = r._master_expiry_resolver()
    assert resolve("NSE:NIFTY2681824000CE") is None
