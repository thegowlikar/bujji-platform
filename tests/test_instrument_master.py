"""InstrumentMaster — NFO symbol-master parsing and ATM resolution.

Column layout verified directly against a live download of FYERS's public
NSE_FO.csv (see docs/FYERS_TRANSPORT_READINESS.md). These tests use a small,
real-shaped CSV fixture (actual rows copied from that live download) rather
than a live network call, so the suite has no network dependency; a separate
live verification proved the real download+parse path end-to-end.

Expiry epochs are computed relative to the actual test run time (not
hardcoded) — ``resolve_atm`` filters candidates against real wall-clock
``datetime.now()``, so a fixture with a fixed past-relative "nearest expiry"
timestamp silently goes stale and starts exercising the wrong code path
(falling through to a later, differently-formatted symbol) the moment
calendar time moves past it. Computing the two expiries as "N days from now"
keeps this fixture valid indefinitely.
"""
import time

import pytest

from bujji.broker.instrument_master import InstrumentMaster
from bujji.core.enums import OptionType
from datetime import datetime, timezone

# Two expiries, always in the future relative to whenever this suite runs:
# a "nearest" weekly-style expiry and a "later" monthly-style expiry, exactly
# as FYERS lists them side by side for the same underlying/strike.
_NOW = int(time.time())
_NEAR_EXPIRY_EPOCH = _NOW + 7 * 86400
_FAR_EXPIRY_EPOCH = _NOW + 28 * 86400
_NEAR_EXPIRY_ISO = datetime.fromtimestamp(_NEAR_EXPIRY_EPOCH, tz=timezone.utc).date().isoformat()

# Real rows from a live download (2026-07-06), trimmed to the columns that
# matter, padded to the real column count so index-based parsing is exercised
# exactly as it is against the genuine file. Symbol strings are the genuine
# FYERS text (weekly rows use the numeric-date convention, e.g.
# "NIFTY2670724400CE"; the monthly row uses the month-abbreviation
# convention, e.g. "NIFTY26JUL24400CE") — only the expiry EPOCH column is
# recomputed per-run so "nearest" always resolves to the weekly row.
_REAL_ROWS = [
    # NIFTY future — must be skipped (option_type "XX", not CE/PE).
    f"101126072861093,NIFTY 28 Jul 26 FUT,11,65,0.1,,0915-1530|1815-1915:,2026-07-03,{_FAR_EXPIRY_EPOCH},NSE:NIFTY26JULFUT,10,11,61093,NIFTY,26000,-1.0,XX,101000000026000,None,0,0.0",
    # Nearest weekly expiry, several strikes around 24400.
    f"101126070735400,NIFTY 07 Jul 26 24350 CE,14,65,0.05,,0915-1530|1815-1915:,2026-07-03,{_NEAR_EXPIRY_EPOCH},NSE:NIFTY2670724350CE,10,11,35400,NIFTY,26000,24350.0,CE,101000000026000,None,0,0.0",
    f"101126070735401,NIFTY 07 Jul 26 24350 PE,14,65,0.05,,0915-1530|1815-1915:,2026-07-03,{_NEAR_EXPIRY_EPOCH},NSE:NIFTY2670724350PE,10,11,35401,NIFTY,26000,24350.0,PE,101000000026000,None,0,0.0",
    f"101126070735402,NIFTY 07 Jul 26 24400 CE,14,65,0.05,,0915-1530|1815-1915:,2026-07-03,{_NEAR_EXPIRY_EPOCH},NSE:NIFTY2670724400CE,10,11,35402,NIFTY,26000,24400.0,CE,101000000026000,None,0,0.0",
    f"101126070735403,NIFTY 07 Jul 26 24400 PE,14,65,0.05,,0915-1530|1815-1915:,2026-07-03,{_NEAR_EXPIRY_EPOCH},NSE:NIFTY2670724400PE,10,11,35403,NIFTY,26000,24400.0,PE,101000000026000,None,0,0.0",
    f"101126070735404,NIFTY 07 Jul 26 24450 CE,14,65,0.05,,0915-1530|1815-1915:,2026-07-03,{_NEAR_EXPIRY_EPOCH},NSE:NIFTY2670724450CE,10,11,35404,NIFTY,26000,24450.0,CE,101000000026000,None,0,0.0",
    f"101126070735405,NIFTY 07 Jul 26 24450 PE,14,65,0.05,,0915-1530|1815-1915:,2026-07-03,{_NEAR_EXPIRY_EPOCH},NSE:NIFTY2670724450PE,10,11,35405,NIFTY,26000,24450.0,PE,101000000026000,None,0,0.0",
    # A later (monthly) expiry, same strikes — proves "nearest" picks the weekly row.
    f"101126072835020,NIFTY 28 Jul 26 24400 CE,14,65,0.05,,0915-1530|1815-1915:,2026-07-03,{_FAR_EXPIRY_EPOCH},NSE:NIFTY26JUL24400CE,10,11,35420,NIFTY,26000,24400.0,CE,101000000026000,None,0,0.0",
    # BANKNIFTY row — must be excluded by underlying filter.
    f"101126072835018,BANKNIFTY 28 Jul 26 51000 CE,14,120,0.05,,0915-1530|1815-1915:,2026-07-03,{_FAR_EXPIRY_EPOCH},NSE:BANKNIFTY26JUL51000CE,10,11,35018,BANKNIFTY,26009,51000.0,CE,101000000026074,None,0,0.0",
]


@pytest.fixture
def master(tmp_path):
    im = InstrumentMaster(tmp_path, __import__("logging").getLogger("test"))
    im._cache_file.write_text("\n".join(_REAL_ROWS))  # noqa: SLF001
    return im


@pytest.mark.asyncio
async def test_resolves_atm_put_at_nearest_expiry(master):
    contract = await master.resolve_atm(
        "NIFTY", spot=24410.0, option_type=OptionType.PE,
        strike_interval=50, lot_size=75,
    )
    assert contract.symbol == "NSE:NIFTY2670724400PE"  # Nearest strike (24400), nearest expiry.
    assert contract.strike == 24400
    assert contract.expiry == _NEAR_EXPIRY_ISO


@pytest.mark.asyncio
async def test_resolves_atm_call(master):
    contract = await master.resolve_atm(
        "NIFTY", spot=24410.0, option_type=OptionType.CE,
        strike_interval=50, lot_size=75,
    )
    assert contract.symbol == "NSE:NIFTY2670724400CE"


@pytest.mark.asyncio
async def test_prefers_nearest_expiry_over_later_one_at_same_strike(master):
    """Both the weekly and monthly listings have a 24400 CE row — must pick
    the weekly (nearer) one."""
    contract = await master.resolve_atm(
        "NIFTY", spot=24400.0, option_type=OptionType.CE,
        strike_interval=50, lot_size=75,
    )
    assert contract.symbol == "NSE:NIFTY2670724400CE"
    assert contract.expiry == _NEAR_EXPIRY_ISO


@pytest.mark.asyncio
async def test_excludes_futures_rows(master):
    """A futures row (option_type XX) for the same underlying must never be
    mistaken for an option contract."""
    rows = master._rows_for("NIFTY")  # noqa: SLF001
    assert all(r.option_type in ("CE", "PE") for r in rows)


@pytest.mark.asyncio
async def test_excludes_other_underlyings(master):
    rows = master._rows_for("NIFTY")  # noqa: SLF001
    assert all(r.underlying == "NIFTY" for r in rows)
    assert master._rows_for("BANKNIFTY")  # noqa: SLF001 - present but separate.


@pytest.mark.asyncio
async def test_unknown_underlying_raises_lookup_error(master):
    with pytest.raises(LookupError):
        await master.resolve_atm("FINNIFTY", 20000.0, OptionType.CE, 50, 40)


@pytest.mark.asyncio
async def test_cache_not_stale_skips_redownload(master, monkeypatch):
    called = {"n": 0}
    def fake_download():
        called["n"] += 1
    monkeypatch.setattr(master, "_download", fake_download)
    await master._ensure_fresh()  # noqa: SLF001 - cache file already fresh (just written).
    assert called["n"] == 0


@pytest.mark.asyncio
async def test_cache_stale_triggers_redownload(master, monkeypatch):
    import os
    old = time.time() - 100000
    os.utime(master._cache_file, (old, old))  # noqa: SLF001
    called = {"n": 0}
    def fake_download():
        called["n"] += 1
    monkeypatch.setattr(master, "_download", fake_download)
    await master._ensure_fresh()  # noqa: SLF001
    assert called["n"] == 1


@pytest.mark.asyncio
async def test_resolve_atm_uses_canonical_rounding_at_exact_midpoint(tmp_path):
    """resolve_atm() must route through Broker.atm_strike() (round-half-up),
    not an independently reimplemented formula that could drift from it and
    silently resolve an exact-midpoint spot to the wrong strike."""
    im = InstrumentMaster(tmp_path, __import__("logging").getLogger("test"))
    rows = [
        f"101126070735402,NIFTY 07 Jul 26 24400 CE,14,65,0.05,,0915-1530|1815-1915:,2026-07-03,{_NEAR_EXPIRY_EPOCH},NSE:NIFTY2670724400CE,10,11,35402,NIFTY,26000,24400.0,CE,101000000026000,None,0,0.0",
        f"101126070735404,NIFTY 07 Jul 26 24450 CE,14,65,0.05,,0915-1530|1815-1915:,2026-07-03,{_NEAR_EXPIRY_EPOCH},NSE:NIFTY2670724450CE,10,11,35404,NIFTY,26000,24450.0,CE,101000000026000,None,0,0.0",
    ]
    im._cache_file.write_text("\n".join(rows))  # noqa: SLF001

    # 24425 is exactly 25 away from both 24400 and 24450 — an exact midpoint.
    contract = await im.resolve_atm(
        "NIFTY", spot=24425.0, option_type=OptionType.CE,
        strike_interval=50, lot_size=75,
    )
    assert contract.strike == 24450  # Must match Broker.atm_strike(24425, 50).


@pytest.mark.asyncio
async def test_lot_size_is_read_from_the_live_row_not_the_config_parameter(master):
    """The exact finding from the 2026-07-19 Capital Management Engine v2
    audit: NIFTY's real, live exchange lot size (65, per this fixture's
    genuine live-downloaded row) must win over whatever the caller passes
    as the `lot_size` parameter -- previously the parameter was stamped on
    unconditionally, silently ignoring the exchange's actual lot size."""
    contract = await master.resolve_atm(
        "NIFTY", spot=24410.0, option_type=OptionType.CE,
        strike_interval=50, lot_size=999,  # Deliberately wrong -- must be ignored.
    )
    assert contract.lot_size == 65  # From the row, not the 999 parameter.


@pytest.mark.asyncio
async def test_lot_size_falls_back_to_parameter_when_row_value_is_invalid(tmp_path):
    """A malformed lot-size column (0, negative, non-numeric) must fall
    back to the caller-supplied config value rather than sizing an order
    off a zero/garbage quantity."""
    im = InstrumentMaster(tmp_path, __import__("logging").getLogger("test"))
    bad_row = (
        f"101126070735402,NIFTY 07 Jul 26 24400 CE,14,0,0.05,,0915-1530|1815-1915:,"
        f"2026-07-03,{_NEAR_EXPIRY_EPOCH},NSE:NIFTY2670724400CE,10,11,35402,NIFTY,"
        f"26000,24400.0,CE,101000000026000,None,0,0.0"
    )
    im._cache_file.write_text(bad_row)  # noqa: SLF001

    contract = await im.resolve_atm(
        "NIFTY", spot=24400.0, option_type=OptionType.CE,
        strike_interval=50, lot_size=75,
    )
    assert contract.lot_size == 75  # Fell back to the config parameter.


# --- Phase 17I.5/17I.6 — additive futures resolution -----------------------
# `_REAL_ROWS`' one NIFTY future (option_type "XX", NSE:NIFTY26JULFUT, expiry
# = _FAR_EXPIRY_EPOCH) is already present above -- these tests exercise the
# new additive read path against it, without touching `_rows_for()` or
# `resolve_atm()`'s own behavior (already proven unchanged by
# `test_excludes_futures_rows`, still passing).
_FAR_EXPIRY_ISO = datetime.fromtimestamp(_FAR_EXPIRY_EPOCH, tz=timezone.utc).date().isoformat()


@pytest.mark.asyncio
async def test_resolve_nearest_future_returns_the_real_symbol_and_expiry(master):
    symbol, expiry_iso, lot_size = await master.resolve_nearest_future("NIFTY")
    assert symbol == "NSE:NIFTY26JULFUT"
    assert expiry_iso == _FAR_EXPIRY_ISO
    assert lot_size == 65  # Live exchange lot size, same column as options.


@pytest.mark.asyncio
async def test_futures_rows_are_discoverable_without_touching_option_rows(master):
    """The additive futures read path must not leak into, or be affected
    by, `_rows_for()`'s own CE/PE-only cache -- the two must stay
    completely independent."""
    futures_rows = master._futures_rows_for("NIFTY")  # noqa: SLF001
    assert len(futures_rows) == 1
    assert futures_rows[0].option_type == "XX"
    assert futures_rows[0].symbol == "NSE:NIFTY26JULFUT"

    # Option parsing remains completely unaffected.
    option_rows = master._rows_for("NIFTY")  # noqa: SLF001
    assert all(r.option_type in ("CE", "PE") for r in option_rows)


@pytest.mark.asyncio
async def test_resolve_nearest_future_unknown_underlying_raises_lookup_error(master):
    with pytest.raises(LookupError):
        await master.resolve_nearest_future("FINNIFTY")


@pytest.mark.asyncio
async def test_resolve_nearest_future_picks_soonest_of_multiple_expiries(tmp_path):
    """Mirrors `test_prefers_nearest_expiry_over_later_one_at_same_strike`'s
    own guarantee for options -- multiple futures months listed must
    resolve to the soonest one, not just the first row in the file."""
    near = _NOW + 5 * 86400
    far = _NOW + 33 * 86400
    rows = [
        f"101126072861099,NIFTY 33 Day FUT,11,65,0.1,,0915-1530|1815-1915:,2026-07-03,{far},NSE:NIFTY26FARFUT,10,11,61099,NIFTY,26000,-1.0,XX,101000000026000,None,0,0.0",
        f"101126072861098,NIFTY 5 Day FUT,11,65,0.1,,0915-1530|1815-1915:,2026-07-03,{near},NSE:NIFTY26NEARFUT,10,11,61098,NIFTY,26000,-1.0,XX,101000000026000,None,0,0.0",
    ]
    im = InstrumentMaster(tmp_path, __import__("logging").getLogger("test"))
    im._cache_file.write_text("\n".join(rows))  # noqa: SLF001

    symbol, expiry_iso, lot_size = await im.resolve_nearest_future("NIFTY")
    assert symbol == "NSE:NIFTY26NEARFUT"
