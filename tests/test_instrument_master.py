"""InstrumentMaster — NFO symbol-master parsing and ATM resolution.

Column layout verified directly against a live download of FYERS's public
NSE_FO.csv (see docs/FYERS_TRANSPORT_READINESS.md). These tests use a small,
real-shaped CSV fixture (actual rows copied from that live download) rather
than a live network call, so the suite has no network dependency; a separate
live verification proved the real download+parse path end-to-end.
"""
import time

import pytest

from bujji.broker.instrument_master import InstrumentMaster
from bujji.core.enums import OptionType

# Real rows from a live download (2026-07-06), trimmed to the columns that
# matter, padded to the real column count so index-based parsing is exercised
# exactly as it is against the genuine file.
_REAL_ROWS = [
    # NIFTY future — must be skipped (option_type "XX", not CE/PE).
    "101126072861093,NIFTY 28 Jul 26 FUT,11,65,0.1,,0915-1530|1815-1915:,2026-07-03,1785232800,NSE:NIFTY26JULFUT,10,11,61093,NIFTY,26000,-1.0,XX,101000000026000,None,0,0.0",
    # Nearest weekly expiry (2026-07-07), several strikes around 24400.
    "101126070735400,NIFTY 07 Jul 26 24350 CE,14,65,0.05,,0915-1530|1815-1915:,2026-07-03,1783418400,NSE:NIFTY2670724350CE,10,11,35400,NIFTY,26000,24350.0,CE,101000000026000,None,0,0.0",
    "101126070735401,NIFTY 07 Jul 26 24350 PE,14,65,0.05,,0915-1530|1815-1915:,2026-07-03,1783418400,NSE:NIFTY2670724350PE,10,11,35401,NIFTY,26000,24350.0,PE,101000000026000,None,0,0.0",
    "101126070735402,NIFTY 07 Jul 26 24400 CE,14,65,0.05,,0915-1530|1815-1915:,2026-07-03,1783418400,NSE:NIFTY2670724400CE,10,11,35402,NIFTY,26000,24400.0,CE,101000000026000,None,0,0.0",
    "101126070735403,NIFTY 07 Jul 26 24400 PE,14,65,0.05,,0915-1530|1815-1915:,2026-07-03,1783418400,NSE:NIFTY2670724400PE,10,11,35403,NIFTY,26000,24400.0,PE,101000000026000,None,0,0.0",
    "101126070735404,NIFTY 07 Jul 26 24450 CE,14,65,0.05,,0915-1530|1815-1915:,2026-07-03,1783418400,NSE:NIFTY2670724450CE,10,11,35404,NIFTY,26000,24450.0,CE,101000000026000,None,0,0.0",
    "101126070735405,NIFTY 07 Jul 26 24450 PE,14,65,0.05,,0915-1530|1815-1915:,2026-07-03,1783418400,NSE:NIFTY2670724450PE,10,11,35405,NIFTY,26000,24450.0,PE,101000000026000,None,0,0.0",
    # A later (monthly) expiry, same strikes — proves "nearest" picks 07-Jul.
    "101126072835020,NIFTY 28 Jul 26 24400 CE,14,65,0.05,,0915-1530|1815-1915:,2026-07-03,1785232800,NSE:NIFTY26JUL24400CE,10,11,35420,NIFTY,26000,24400.0,CE,101000000026000,None,0,0.0",
    # BANKNIFTY row — must be excluded by underlying filter.
    "101126072835018,BANKNIFTY 28 Jul 26 51000 CE,14,120,0.05,,0915-1530|1815-1915:,2026-07-03,1785232800,NSE:BANKNIFTY26JUL51000CE,10,11,35018,BANKNIFTY,26009,51000.0,CE,101000000026074,None,0,0.0",
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
    assert contract.expiry == "2026-07-07"


@pytest.mark.asyncio
async def test_resolves_atm_call(master):
    contract = await master.resolve_atm(
        "NIFTY", spot=24410.0, option_type=OptionType.CE,
        strike_interval=50, lot_size=75,
    )
    assert contract.symbol == "NSE:NIFTY2670724400CE"


@pytest.mark.asyncio
async def test_prefers_nearest_expiry_over_later_one_at_same_strike(master):
    """Both 07-Jul and 28-Jul have a 24400 CE row — must pick 07-Jul."""
    contract = await master.resolve_atm(
        "NIFTY", spot=24400.0, option_type=OptionType.CE,
        strike_interval=50, lot_size=75,
    )
    assert contract.symbol == "NSE:NIFTY2670724400CE"
    assert contract.expiry == "2026-07-07"


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
