"""Phase 17J.2 (revisit) — Situation Similarity tests.

Mechanics-only. This test suite verifies the code does what it says
(exact-match fraction, missing-dimension handling, vix bucketing,
ranking) -- it does NOT assert the similarity scores themselves are
"good" discrimination. See
docs/PHASE_17J2_REVISIT_VALIDATION_FINDING.md for the real, honest
validation result against actual historical dates, which found the
mechanics work correctly but discrimination quality is currently weak.
"""
import sys
import tempfile
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from bujji.market_understanding.similarity import (
    ALL_DIMENSIONS, SituationFeatureVector, compare, find_similar, vix_band,
)


def _vector(**overrides):
    base = dict(
        instrument_identity="NSE:NIFTY50-INDEX", date="2026-08-01", as_of="2026-08-01T15:25:00+05:30",
        trend_state="TREND_NONE", swing_state="CONFIRMED", compression_state="NOT_DETECTED",
        expansion_state="EARLY", balance_state="IN_BALANCE", structure_state="BALANCE",
        structure_integrity="COHERENT", support_state="ESTABLISHED", resistance_state="WEAK",
        breakout_state="FAILED", breakdown_state="DEVELOPING", retest_state="ACTIVE",
        rejection_state="STRONG", structural_balance="UNBOUNDED", structure_location="AT_RETEST",
        vix_close=15.0, vix_band="NORMAL", source_observation_ids=("OBS-a",),
    )
    base.update(overrides)
    return SituationFeatureVector(**base)


# --- VIX bucketing ------------------------------------------------------------
def test_vix_band_thresholds():
    assert vix_band(11.9) == "LOW"
    assert vix_band(12.0) == "NORMAL"
    assert vix_band(14.9) == "NORMAL"
    assert vix_band(15.0) == "ELEVATED"
    assert vix_band(19.9) == "ELEVATED"
    assert vix_band(20.0) == "HIGH"
    assert vix_band(29.9) == "HIGH"
    assert vix_band(30.0) == "EXTREME"
    assert vix_band(72.0) == "EXTREME"


def test_vix_band_none_for_missing_vix():
    assert vix_band(None) is None


# --- compare(): exact-match fraction -------------------------------------------
def test_compare_identical_vectors_is_1():
    a = _vector()
    b = _vector(date="2026-08-02", as_of="2026-08-02T15:25:00+05:30")
    assert compare(a, b) == 1.0


def test_compare_totally_different_vectors_is_0():
    a = _vector()
    b = _vector(
        trend_state="TREND_ESTABLISHED", swing_state="FORMING", compression_state="EARLY",
        expansion_state="NOT_DETECTED", balance_state="IMBALANCED", structure_state="TRENDING",
        structure_integrity="CONTRADICTORY", support_state="NONE", resistance_state="ESTABLISHED",
        breakout_state="CONFIRMED", breakdown_state="NONE", retest_state="NONE",
        rejection_state="WEAK", structural_balance="BOUNDED", structure_location="NEAR_SUPPORT",
        vix_band="EXTREME",
    )
    assert compare(a, b) == 0.0


def test_compare_partial_match_fraction():
    a = _vector()
    # Change exactly 3 of the 16 dimensions.
    b = _vector(trend_state="TREND_ESTABLISHED", vix_band="EXTREME", support_state="NONE")
    score = compare(a, b)
    assert score == (len(ALL_DIMENSIONS) - 3) / len(ALL_DIMENSIONS)


def test_compare_excludes_missing_dimensions_from_denominator():
    a = _vector(vix_band=None)
    b = _vector(vix_band="EXTREME")
    # vix_band is missing on `a` -- must be excluded, never counted as a mismatch.
    score = compare(a, b)
    assert score == 1.0  # every OTHER dimension matches; vix_band excluded entirely.


def test_compare_returns_none_when_nothing_is_comparable():
    a = _vector(**{d: None for d in ALL_DIMENSIONS})
    b = _vector()
    assert compare(a, b) is None


# --- find_similar(): ranking + self-exclusion ------------------------------------
def test_find_similar_ranks_descending():
    target = _vector()
    close = _vector(date="2026-08-02", as_of="2026-08-02T15:25:00+05:30", vix_band="ELEVATED")
    far = _vector(
        date="2026-08-03", as_of="2026-08-03T15:25:00+05:30",
        trend_state="TREND_ESTABLISHED", vix_band="EXTREME", support_state="NONE",
        resistance_state="ESTABLISHED", breakout_state="CONFIRMED",
    )
    ranked = find_similar(target, [far, close], top_n=5)
    assert [c.date for c, _ in ranked] == ["2026-08-02", "2026-08-03"]


def test_find_similar_excludes_self():
    target = _vector()
    exact_self = _vector()  # same instrument + as_of.
    other = _vector(date="2026-08-02", as_of="2026-08-02T15:25:00+05:30")
    ranked = find_similar(target, [exact_self, other], top_n=5)
    assert [c.date for c, _ in ranked] == ["2026-08-02"]


def test_find_similar_respects_top_n():
    target = _vector()
    candidates = [
        _vector(date=f"2026-08-{d:02d}", as_of=f"2026-08-{d:02d}T15:25:00+05:30")
        for d in range(2, 10)
    ]
    ranked = find_similar(target, candidates, top_n=3)
    assert len(ranked) == 3
