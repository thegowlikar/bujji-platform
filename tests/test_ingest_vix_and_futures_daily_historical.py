"""Phase 17H.6 — VIX and Futures continuous daily historical ingestion
scripts. Mirrors test_ingest_nifty_spot_daily_historical.py.
"""
import datetime
import importlib.util
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, _REPO_ROOT / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


vix_ingest = _load("ingest_india_vix_daily_historical", "ingest_india_vix_daily_historical.py")
fut_ingest = _load("ingest_nifty_futures_daily_historical", "ingest_nifty_futures_daily_historical.py")


# --- VIX ingestion ---------------------------------------------------------------
def test_vix_chunks_respect_366_day_limit():
    chunks = list(vix_ingest._chunks(datetime.date(2008, 1, 1), datetime.date(2026, 8, 13),
                                      vix_ingest.CHUNK_DAYS))
    for s, e in chunks:
        assert (e - s).days + 1 <= 366


def test_vix_default_start_brackets_the_certified_earliest_date():
    """Real, live-bisected/certified earliest VIX date is 2008-04-17 --
    the default start must bracket it, not assume spot's ~1998."""
    assert vix_ingest.DEFAULT_START == datetime.date(2008, 1, 1)
    assert vix_ingest.DEFAULT_START < datetime.date(2008, 4, 17)


def test_vix_instrument_identity_equals_request_symbol():
    """VIX has no continuous-series distinction -- unlike futures."""
    assert vix_ingest.INSTRUMENT_IDENTITY == vix_ingest.SYMBOL


def test_vix_not_market_hours_gated():
    assert not hasattr(vix_ingest, "within_market_hours")


def test_vix_validate_row_rejects_the_real_negative_sentinel_found_live():
    """Real row rejected during the 2026-08-13 live ingestion:
    [1613088000, -1.0, -1.0, -1.0, 23.05, 0] -- a genuine FYERS data
    artifact, correctly caught, never silently accepted."""
    row = [1613088000, -1.0, -1.0, -1.0, 23.05, 0]
    assert vix_ingest._validate_row(row) is not None


# --- Futures ingestion -------------------------------------------------------------
def test_futures_chunks_respect_366_day_limit():
    chunks = list(fut_ingest._chunks(datetime.date(2018, 1, 1), datetime.date(2026, 8, 13),
                                      fut_ingest.CHUNK_DAYS))
    for s, e in chunks:
        assert (e - s).days + 1 <= 366


def test_futures_default_start_brackets_the_live_bisected_earliest_date():
    """Real finding, Phase 17H.6: continuous futures depth is
    structurally shallower than spot -- 2017=no_data, 2018=ok,
    live-bisected 2026-08-13. Must NOT default to spot's ~1998."""
    assert fut_ingest.DEFAULT_START == datetime.date(2018, 1, 1)
    assert fut_ingest.DEFAULT_START > datetime.date(2017, 12, 31)


def test_futures_instrument_identity_is_never_the_request_symbol():
    """PHASE_17H3 Part 2.4's binding rule: identity is fixed and
    continuous, never the literal contract symbol used for the request."""
    assert fut_ingest.INSTRUMENT_IDENTITY == "NIFTY_FUT_CONTINUOUS"
    assert fut_ingest.INSTRUMENT_IDENTITY != "NSE:NIFTY26AUGFUT"


def test_futures_continuity_method_constant():
    assert fut_ingest.CONTINUITY_METHOD == "fyers_cont_flag_1"


def test_futures_not_market_hours_gated():
    assert not hasattr(fut_ingest, "within_market_hours")


def test_futures_validate_row_accepts_a_real_row():
    row = [1514851200, 10521.2, 10524.0, 10436.5, 10472.2, 9306380]  # Real 2018-01-02 row.
    assert fut_ingest._validate_row(row) is None
