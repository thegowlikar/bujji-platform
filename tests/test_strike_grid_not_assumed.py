"""Half the option chain was silently discarded by an assumed strike grid.

THE DEFECT. `resolve_chain_contracts` filtered every candidate through

    if strike % config.strike_step != 0:
        continue

with `OptionChainConfig.strike_step` defaulting to 100. NIFTY's real grid step
is 50. `bujji_options_os_runner.py` builds its MarketDataAdapter WITHOUT a
chain_config, so production took that default.

MEASURED against the live instrument master, nearest expiry, spot 24216.65:

    strikes within the configured +/-2000 band   160
    kept by strike_step=100                       80
    silently dropped                              80   (50%)
    observed grid step                            50

This fed the intelligence/regime path -- market thesis, IV surface, OI walls,
positioning. Every one of them was computed from a chain with every other
strike missing, and nothing reported it: a filter that skips rows looks exactly
like a chain that is simply smaller.

THE INVARIANT. The rows come from the exchange's own symbol master. A strike
present there for that expiry IS a real, tradable, on-grid contract. Testing it
against an ASSUMED step can only discard reality; it cannot add safety, because
a contract that does not exist was never in the list.

`strike_step` is deliberately retained on the config: recovery.py rebuilds
persisted snapshots via `OptionChainConfig(**c["config"])`.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from bujji.market_perception.models import OptionChainConfig  # noqa: E402
from bujji.market_perception.option_chain_adapter import (  # noqa: E402
    _observed_strike_step, resolve_chain_contracts,
)
from bujji.broker import instrument_master as im  # noqa: E402

FAR_FUTURE = 4102444800  # 2100-01-01, so the expiry filter never drops rows


def _row(strike, opt_type, symbol, epoch=FAR_FUTURE, underlying="NIFTY", lot=75):
    r = [""] * im._MIN_COLUMNS
    r[im._COL_EXPIRY_EPOCH] = str(epoch)
    r[im._COL_SYMBOL] = symbol
    r[im._COL_UNDERLYING] = underlying
    r[im._COL_STRIKE] = str(float(strike))
    r[im._COL_OPTION_TYPE] = opt_type
    r[3] = str(lot)
    return ",".join(r)


def _master(tmp_path, strikes, step_label="x"):
    lines = []
    for k in strikes:
        for t in ("CE", "PE"):
            lines.append(_row(k, t, f"NSE:NIFTY{step_label}{int(k)}{t}"))
    p = tmp_path / "fyers_fo_NSE.csv"
    p.write_text("\n".join(lines) + "\n")
    return str(p)


class TestNoValidStrikeIsSilentlyDiscarded:
    def test_every_50_point_strike_survives(self, tmp_path):
        """THE regression. A real NIFTY grid: 24000..24500 step 50."""
        strikes = list(range(24000, 24501, 50))
        master = _master(tmp_path, strikes)
        cfg = OptionChainConfig(strike_range=2000, strike_step=100)

        _expiry, contracts = resolve_chain_contracts("NIFTY", 24250.0, cfg, master)
        got = sorted({int(c.strike) for c in contracts})

        assert got == strikes, (
            f"expected all {len(strikes)} strikes, kept {len(got)}. "
            f"Missing: {sorted(set(strikes) - set(got))}")

    def test_the_odd_50s_are_exactly_what_used_to_vanish(self, tmp_path):
        """Named explicitly, because these are the 50% that disappeared."""
        strikes = list(range(24000, 24501, 50))
        master = _master(tmp_path, strikes)
        cfg = OptionChainConfig(strike_range=2000, strike_step=100)

        _e, contracts = resolve_chain_contracts("NIFTY", 24250.0, cfg, master)
        kept = {int(c.strike) for c in contracts}
        odd_50s = [k for k in strikes if k % 100 != 0]

        assert odd_50s, "fixture is wrong -- it contains no odd-50 strikes"
        missing = [k for k in odd_50s if k not in kept]
        assert not missing, f"odd-50 strikes still discarded: {missing}"

    def test_both_option_types_survive(self, tmp_path):
        master = _master(tmp_path, [24050, 24100, 24150])
        cfg = OptionChainConfig(strike_range=2000, strike_step=100)
        _e, contracts = resolve_chain_contracts("NIFTY", 24100.0, cfg, master)
        for strike in (24050, 24150):
            types = {c.option_type.value for c in contracts if int(c.strike) == strike}
            assert types == {"CE", "PE"}, f"strike {strike} lost a leg: {types}"


class TestTheRangeIsStillHonoured:
    def test_strikes_outside_the_band_are_still_excluded(self, tmp_path):
        """Removing the grid filter must not remove the RANGE filter."""
        strikes = [20000, 24000, 24050, 28000]
        master = _master(tmp_path, strikes)
        cfg = OptionChainConfig(strike_range=500, strike_step=100)

        _e, contracts = resolve_chain_contracts("NIFTY", 24000.0, cfg, master)
        kept = sorted({int(c.strike) for c in contracts})
        assert kept == [24000, 24050], f"range filter broken: kept {kept}"


class TestTheObservedGridIsMeasuredNotAssumed:
    @pytest.mark.parametrize("strikes,expected", [
        ([24000, 24050, 24100], 50),      # NIFTY
        ([39900, 40000, 40100], 100),     # BANKNIFTY
        ([100, 400, 700], 300),           # an underlying nobody hardcoded
    ])
    def test_step_is_derived_from_the_contracts(self, tmp_path, strikes, expected):
        master = _master(tmp_path, strikes)
        cfg = OptionChainConfig(strike_range=5000, strike_step=100)
        _e, contracts = resolve_chain_contracts("NIFTY", float(strikes[1]), cfg, master)
        assert _observed_strike_step(contracts, fallback=cfg.strike_step) == expected

    def test_a_single_strike_falls_back_to_the_configured_step(self):
        class _C:
            strike = 24000
        assert _observed_strike_step([_C()], fallback=100) == 100
        assert _observed_strike_step([], fallback=50) == 50

    def test_it_never_returns_zero(self):
        class _C:
            def __init__(self, k): self.strike = k
        assert _observed_strike_step([_C(1), _C(1)], fallback=0) >= 1


class TestTheConfigFieldSurvives:
    def test_strike_step_still_deserialises(self):
        """market_state_builder/recovery.py rebuilds persisted snapshots with
        OptionChainConfig(**c["config"]) -- the field must not be removed."""
        cfg = OptionChainConfig(**{"strike_range": 2000, "strike_step": 100})
        assert cfg.strike_step == 100 and cfg.strike_range == 2000
