"""Phase 17H.4 — NIFTY Spot Daily Historical Ingestion script.

Loaded by file path, same posture as every other operational script in
this project. `run()`'s live-broker path requires real credentials, so
what's tested here is the pure logic: chunking (must respect the
366-day per-request limit, PHASE_17H1 §1.1) and per-row validation
(PHASE_17H2 Part 6 rules V1/V2).
"""
import datetime
import importlib.util
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "ingest_nifty_spot_daily_historical.py"
_spec = importlib.util.spec_from_file_location(
    "ingest_nifty_spot_daily_historical", _SCRIPT_PATH
)
ingest_script = importlib.util.module_from_spec(_spec)
sys.modules["ingest_nifty_spot_daily_historical"] = ingest_script
_spec.loader.exec_module(ingest_script)


# --- Chunking -----------------------------------------------------------------
def test_chunks_respect_the_366_day_limit():
    """Real, measured API limit (PHASE_17H1 §1.1): no chunk may exceed
    366 days."""
    start = datetime.date(1998, 1, 1)
    end = datetime.date(2026, 8, 13)
    chunks = list(ingest_script._chunks(start, end, ingest_script.CHUNK_DAYS))
    for chunk_start, chunk_end in chunks:
        span_days = (chunk_end - chunk_start).days + 1
        assert span_days <= 366, f"{chunk_start}..{chunk_end} spans {span_days} days"


def test_chunks_cover_the_full_range_with_no_gaps_or_overlaps():
    start = datetime.date(2020, 1, 1)
    end = datetime.date(2022, 6, 15)
    chunks = list(ingest_script._chunks(start, end, ingest_script.CHUNK_DAYS))

    assert chunks[0][0] == start
    assert chunks[-1][1] == end
    for (prev_start, prev_end), (next_start, _next_end) in zip(chunks, chunks[1:]):
        assert next_start == prev_end + datetime.timedelta(days=1)  # No gap, no overlap.


def test_chunks_single_day_range_yields_one_chunk():
    d = datetime.date(2026, 8, 13)
    chunks = list(ingest_script._chunks(d, d, ingest_script.CHUNK_DAYS))
    assert chunks == [(d, d)]


def test_chunk_days_matches_the_measured_limit():
    assert ingest_script.CHUNK_DAYS == 366


# --- Row validation (PHASE_17H2 Part 6, V1/V2) --------------------------------
def test_validate_row_accepts_a_real_row():
    row = [894240000, 1159.8, 1185.75, 1159.8, 1185.15, 0]
    assert ingest_script._validate_row(row) is None


def test_validate_row_rejects_non_positive_ohlc():
    row = [894240000, -1.0, 1.0, -1.0, 1.0, 0]
    assert ingest_script._validate_row(row) is not None


def test_validate_row_rejects_impossible_high():
    """high must be >= open/close/low -- V1."""
    row = [894240000, 100.0, 50.0, 40.0, 90.0, 0]  # high (50) < open (100)
    assert ingest_script._validate_row(row) is not None


def test_validate_row_rejects_impossible_low():
    row = [894240000, 100.0, 120.0, 110.0, 90.0, 0]  # low (110) > close (90)
    assert ingest_script._validate_row(row) is not None


def test_validate_row_rejects_short_rows():
    assert ingest_script._validate_row([894240000, 100.0]) is not None


# --- Not market-hours-gated (PHASE_17H3 Part 1.5) ------------------------------
def test_script_has_no_market_hours_gate():
    """Historical data has no market-hours dependency -- unlike every
    live/discovery script, this one must not import or define a
    within_market_hours()-style gate."""
    assert not hasattr(ingest_script, "within_market_hours")
    assert not hasattr(ingest_script, "MARKET_OPEN")
    assert not hasattr(ingest_script, "MARKET_CLOSE")


# --- Instrument identity rule (PHASE_17H3 Part 2.4) ----------------------------
def test_instrument_identity_equals_request_symbol_for_spot():
    """Spot has no continuous-series identity distinction -- unlike
    futures, its request symbol IS its real identity."""
    assert ingest_script.INSTRUMENT_IDENTITY == ingest_script.SYMBOL
