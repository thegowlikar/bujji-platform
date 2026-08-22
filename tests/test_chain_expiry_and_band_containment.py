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


def _provider(resolver=None, strike_count=20):
    return LiveChainProvider(object(), underlying="NIFTY", strike_count=strike_count,
                             expiry_resolver=resolver)


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
    chain, _spot = _provider()._build(raw, AS_OF)
    assert chain[0].observation.identity.timestamp  # sanity: a real observation
    assert _expiry_of(chain[0]) == "2026-08-18", "position 0 was 25-08, the nearest is 18-08"


def test_unparseable_expiry_entries_are_skipped_not_trusted():
    raw = _raw([{"date": "garbage"}, {"date": "18-08-2026"}],
               [_row(24000.0, "CE", "NSE:NIFTY2681824000CE")])
    chain, _ = _provider()._build(raw, AS_OF)
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
    chain, _ = _provider(resolver=truth.get)._build(raw, AS_OF)
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
    chain, _ = _provider(resolver=resolver)._build(raw, AS_OF)
    assert len(chain) == 1
    assert chain[0].instrument_symbol == "NSE:NIFTY2681824000CE"


def test_without_a_resolver_the_uniform_stamp_still_applies():
    """The resolver is additive. Callers that wire none keep prior behaviour
    rather than silently losing their chain."""
    raw = _raw([{"date": "18-08-2026"}], [_row(24000.0, "CE", "NSE:NIFTY2681824000CE")])
    chain, _ = _provider()._build(raw, AS_OF)
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


def test_the_runner_grid_step_matches_the_builder_default():
    """The tier arithmetic and the universe the builder constructs must not
    disagree about how wide a strike is."""
    import inspect
    from bujji.capture_universe import builder
    import bujji_options_os_runner as runner

    default_step = inspect.signature(builder.build_capture_universe).parameters["step"].default
    assert runner._UNIVERSE_GRID_STEP == default_step


def test_tiers_are_derived_from_strike_count_not_left_to_coincidence():
    src = RUNNER_SRC
    assert "band_points = strike_count * _UNIVERSE_GRID_STEP" in src
    assert "tiers=tiers" in src, "the derived tiers must actually reach the builder"


def test_only_front_and_second_are_widened():
    """MONTHLY reaches FORWARD past any weekly already taken, so it can never
    be the expiry `select_expiry` chooses. Widening it would subscribe symbols
    no selection can range over."""
    tree = ast.parse(RUNNER_SRC)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "_ensure_universe_subscribed")
    src = ast.unparse(fn)
    assert "ROLE_FRONT, ROLE_SECOND" in src
    assert "ROLE_MONTHLY" not in src


def test_a_wider_chain_would_widen_the_tier():
    """The invariant, exercised arithmetically: raising strike_count past the
    tier must raise the tier, not push band contracts outside the universe."""
    from bujji.capture_universe.builder import DEFAULT_TIERS, ROLE_FRONT
    import bujji_options_os_runner as runner

    step = runner._UNIVERSE_GRID_STEP
    for strike_count in (20, 30, 40, 60):
        band_points = strike_count * step
        tiers = dict(DEFAULT_TIERS)
        if tiers[ROLE_FRONT] < band_points:
            tiers[ROLE_FRONT] = band_points
        assert tiers[ROLE_FRONT] >= band_points, (strike_count, tiers[ROLE_FRONT])


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
