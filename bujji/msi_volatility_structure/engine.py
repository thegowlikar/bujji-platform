"""Volatility Structure Bridge engine — pure functions, no state
(beyond throwaway stateless instantiation of legacy classes solely to
call their pure static/instance methods), no wall-clock reads anywhere
in this module.

---------------------------------------------------------------------
Reuse discipline (Deliverable 2 — every decision justified below):
---------------------------------------------------------------------
- `solve_implied_volatility`, `_annualized_realized_vol`,
  `compute_expected_move` (bujji.intelligence.volatility_brain),
  `VolatilityBrain._classify_richness` (a @staticmethod, callable
  without instantiating the impure class's `now_ist()`-calling
  `analyze()`), `_bs_delta`/`_bs_gamma`/`_bs_theta`/`_bs_vega`
  (bujji.intelligence.greeks_brain) — imported and called DIRECTLY.
  REUSE DECISION: reuse directly. PERMANENT — this pure Black-Scholes
  math is correct, validated, and has no reason to move; this bridge
  imports it, never forks it.
- `RegimeBrain._compression_ratio`/`_log_returns`/`_stdev` and its
  `COMPRESSION_RATIO_THRESHOLD`/`EXPANSION_RATIO_THRESHOLD` module
  constants (bujji.intelligence.regime_brain) — REUSE DECISION: adapt
  (a throwaway `RegimeBrain()` instance is created solely to call its
  pure, state-free instance methods; its impure `analyze()`/`now_ist()`
  path is never touched). TEMPORARY BRIDGE, NOT PERMANENT — disclosed
  explicitly: this is a PRICE-statistics proxy for expansion/
  compression, not a genuine IV-based volatility-structure signal. It
  should be REPLACED once real multi-strike/multi-expiry IV
  time-series data exists to compute expansion/compression from IV
  itself. Tracked as an open migration item in
  docs/VOLATILITY_STRUCTURE_BRIDGE.md Section 6.
- IV Rank, IV Percentile, Skew, Smile, Term Structure, Vanna, Charm —
  genuinely MISSING (Deliverable 1). Never computed here; the
  corresponding assessment fields are always UNKNOWN/None, never
  fabricated.

This module imports NOTHING from any MSI sibling package (78/79/81/
83/85/86/87) directly — it is a pure Observation-to-Volatility-domain
bridge, analogous to how 78/79 consume raw Episode/MarketEvent
evidence, not another brain's conclusions. Its OUTPUT is designed to
be adaptable into a Market Direction lens (Deliverable 5) and a
Consensus domain view (Deliverable 6) by an external, disclosed
translation layer, exactly mirroring MPPI's (Series 86) own Deliverable
7 precedent — not by this package importing those types itself.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from typing import List, Optional, Sequence, Tuple

from bujji.core.enums import OptionType
from bujji.core.models import Candle
from bujji.intelligence.volatility_brain import (
    compute_expected_move,
    solve_implied_volatility,
    VolatilityBrain,
)
from bujji.intelligence.greeks_brain import _bs_delta, _bs_gamma, _bs_theta, _bs_vega
from bujji.intelligence.regime_brain import RegimeBrain, COMPRESSION_RATIO_THRESHOLD, EXPANSION_RATIO_THRESHOLD
from bujji.intelligence.models import Richness

from . import config as _config
from . import taxonomy
from .models import Explanation, VolatilityStructureAssessment

_REGIME_BRAIN = RegimeBrain()  # Stateless; instantiated once, only its pure methods are called.


def _synthetic_candles(closes_with_ts: Sequence[Tuple[str, float]]) -> List[Candle]:
    """Build real Candle objects (open=high=low=close, since only
    `.close` is consulted by the reused realized-vol/compression
    functions) from a real (timestamp, close) sequence -- never
    fabricates a value beyond what was actually observed."""
    out = []
    for ts, close in closes_with_ts:
        dt = datetime.fromisoformat(ts)
        out.append(Candle(timestamp=dt, open=close, high=close, low=close, close=close, volume=0.0))
    return out


def derive_iv_and_expected_move(
    spot: float, strike: float, t_years: float, ce_premium: Optional[float], pe_premium: Optional[float],
    risk_free_rate: float = _config.DEFAULT_RISK_FREE_RATE,
) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """Returns (iv_average, expected_move_points, expected_move_pct).
    Reuses solve_implied_volatility + compute_expected_move DIRECTLY.
    Requires BOTH legs to solve (mirrors VolatilityBrain.analyze()'s
    own both-or-neither discipline) -- never averages a single solved
    leg."""
    if ce_premium is None or pe_premium is None or t_years <= 0 or spot <= 0 or strike <= 0:
        return None, None, None
    iv_ce = solve_implied_volatility(ce_premium, spot, strike, t_years, risk_free_rate, OptionType.CE)
    iv_pe = solve_implied_volatility(pe_premium, spot, strike, t_years, risk_free_rate, OptionType.PE)
    if iv_ce is None or iv_pe is None:
        return None, None, None
    iv_average = (iv_ce + iv_pe) / 2
    expected_move_points = compute_expected_move(spot, iv_average, t_years)
    expected_move_pct = (expected_move_points / spot * 100.0) if spot > 0 else None
    return iv_average, expected_move_points, expected_move_pct


def derive_realized_vol(closes_with_ts: Sequence[Tuple[str, float]]) -> Optional[float]:
    """Reuses `_annualized_realized_vol` DIRECTLY (permanent reuse)."""
    if len(closes_with_ts) < _config.MIN_CANDLES_FOR_REALIZED_VOL:
        return None
    candles = _synthetic_candles(closes_with_ts)
    return VolatilityBrain._annualized_realized_vol(candles)


def derive_iv_state(iv_average: Optional[float], realized_vol: Optional[float]) -> Tuple[str, str]:
    """Reuses VolatilityBrain._classify_richness DIRECTLY (a pure
    @staticmethod, never calling the impure analyze() wrapper)."""
    if iv_average is None or realized_vol is None or realized_vol <= 0:
        return taxonomy.IV_UNKNOWN, "iv_average or realized_vol unavailable -- richness undefined."
    ratio = iv_average / realized_vol
    richness, reason, _confidence = VolatilityBrain._classify_richness(ratio)
    mapping = {Richness.IV_RICH: taxonomy.IV_RICH, Richness.IV_CHEAP: taxonomy.IV_CHEAP, Richness.IV_FAIR: taxonomy.IV_FAIR}
    return mapping.get(richness, taxonomy.IV_UNKNOWN), reason


def derive_expected_move_state(expected_move_pct: Optional[float]) -> str:
    """NEW, disclosed bucketing (classification, not new math) over
    the real expected_move_pct value."""
    if expected_move_pct is None:
        return taxonomy.EXPECTED_MOVE_UNKNOWN
    if expected_move_pct >= taxonomy.EXPECTED_MOVE_WIDE_MIN_PCT:
        return taxonomy.EXPECTED_MOVE_WIDE
    if expected_move_pct >= taxonomy.EXPECTED_MOVE_MODERATE_MIN_PCT:
        return taxonomy.EXPECTED_MOVE_MODERATE
    return taxonomy.EXPECTED_MOVE_NARROW


def derive_regime_expansion_compression(closes_with_ts: Sequence[Tuple[str, float]]) -> Tuple[str, str, str, Optional[float]]:
    """Reuses RegimeBrain._compression_ratio/_log_returns/_stdev
    DIRECTLY (TEMPORARY BRIDGE, price-based proxy -- see module
    docstring). Returns (volatility_regime, expansion_state,
    compression_state, realized_vol_for_regime)."""
    if len(closes_with_ts) < _config.MIN_CANDLES_FOR_REALIZED_VOL:
        return taxonomy.REGIME_UNKNOWN, taxonomy.EXPANSION_UNKNOWN, taxonomy.COMPRESSION_UNKNOWN, None
    closes = [c for _, c in sorted(closes_with_ts, key=lambda x: x[0])]
    returns = _REGIME_BRAIN._log_returns(closes)
    realized_vol = _REGIME_BRAIN._stdev(returns) if len(returns) >= 2 else 0.0
    compression_ratio = _REGIME_BRAIN._compression_ratio(returns)

    from bujji.intelligence.regime_brain import VOL_HIGH_THRESHOLD
    # BUGFIX: a real, computed compression_ratio landing in the
    # ambiguous middle is REAL EVIDENCE of an unremarkable/stable
    # reading, never UNKNOWN (genuinely insufficient data). Confirmed
    # by direct measurement: 100% of a prior UNKNOWN over-count traced
    # to exactly this conflation, not to threshold-scale mismatch.
    if realized_vol >= VOL_HIGH_THRESHOLD:
        regime = taxonomy.REGIME_HIGH_VOLATILITY
    elif compression_ratio is not None and compression_ratio <= COMPRESSION_RATIO_THRESHOLD:
        regime = taxonomy.REGIME_COMPRESSED
    elif compression_ratio is not None and compression_ratio >= EXPANSION_RATIO_THRESHOLD:
        regime = taxonomy.REGIME_TRANSITIONING
    elif compression_ratio is not None:
        regime = taxonomy.REGIME_STABLE
    else:
        regime = taxonomy.REGIME_UNKNOWN

    if compression_ratio is None:
        expansion_state = taxonomy.EXPANSION_UNKNOWN
        compression_state = taxonomy.COMPRESSION_UNKNOWN
    else:
        expansion_state = taxonomy.EXPANSION_CONFIRMED if compression_ratio >= EXPANSION_RATIO_THRESHOLD else taxonomy.EXPANSION_NOT_DETECTED
        compression_state = taxonomy.COMPRESSION_CONFIRMED if compression_ratio <= COMPRESSION_RATIO_THRESHOLD else taxonomy.COMPRESSION_NOT_DETECTED

    return regime, expansion_state, compression_state, realized_vol


def compute_confidence(iv_state: str, volatility_regime: str, has_iv: bool, has_regime: bool) -> str:
    if not has_iv and not has_regime:
        return taxonomy.CONFIDENCE_NONE
    if iv_state != taxonomy.IV_UNKNOWN and volatility_regime != taxonomy.REGIME_UNKNOWN:
        return taxonomy.CONFIDENCE_MODERATE
    if iv_state != taxonomy.IV_UNKNOWN or volatility_regime != taxonomy.REGIME_UNKNOWN:
        return taxonomy.CONFIDENCE_LOW
    return taxonomy.CONFIDENCE_NONE


def build_explanation(
    *, assessment_id: str, iv_state: str, volatility_regime: str, expected_move_state: str,
    expansion_state: str, compression_state: str, iv_reason: str,
) -> Explanation:
    why = [
        f"iv_state={iv_state}: {iv_reason}",
        f"volatility_regime={volatility_regime} derived from realized price-volatility statistics "
        f"(a temporary, disclosed price-based proxy -- see module docstring).",
        f"expected_move_state={expected_move_state} bucketed from the real expected-move percentage.",
        f"expansion_state={expansion_state}/compression_state={compression_state} from the same "
        f"price-volatility proxy as volatility_regime.",
    ]
    missing_evidence = [
        "skew_state and term_structure_state are always UNKNOWN -- no cross-strike IV curve or "
        "multi-expiry IV comparison exists anywhere in this codebase (Deliverable 1: genuinely missing).",
        "IV Rank/IV Percentile are not computed -- require weeks/months of historical IV not yet "
        "available (same disclosed gap as the legacy VolatilityBrain).",
        "Vanna and Charm are not computed -- not implemented anywhere in this codebase.",
    ]
    would_increase_confidence = []
    if iv_state == taxonomy.IV_UNKNOWN:
        would_increase_confidence.append("A solvable IV for both CE and PE legs (real premiums for both currently unavailable or unsolvable).")
    if volatility_regime == taxonomy.REGIME_UNKNOWN:
        would_increase_confidence.append("More real price candles for the realized-volatility/compression-ratio computation.")
    if not would_increase_confidence:
        would_increase_confidence.append("No further evidence currently identified as increasing confidence beyond the present read.")

    return Explanation(
        assessment_id=assessment_id, why=tuple(why), missing_evidence=tuple(missing_evidence),
        would_increase_confidence=tuple(would_increase_confidence), schema_version=_config.SCHEMA_VERSION,
    )


def _assessment_id(
    volatility_regime: str, iv_state: str, expected_move_state: str, skew_state: str,
    term_structure_state: str, expansion_state: str, compression_state: str, confidence: str, schema_version: str,
) -> str:
    seed = "###".join([
        volatility_regime, iv_state, expected_move_state, skew_state, term_structure_state,
        expansion_state, compression_state, confidence, schema_version,
    ])
    return "VSA-" + hashlib.md5(seed.encode()).hexdigest()[:24]


def assess_volatility_structure(
    spot: float, strike: float, t_years: float,
    ce_premium: Optional[float], pe_premium: Optional[float],
    closes_with_ts: Sequence[Tuple[str, float]],
    *, timestamp: str,
    risk_free_rate: float = _config.DEFAULT_RISK_FREE_RATE,
    provenance: str = _config.DEFAULT_PROVENANCE,
    schema_version: str = _config.SCHEMA_VERSION,
) -> VolatilityStructureAssessment:
    """Top-level bridge entrypoint -- pure function of its inputs; no
    wall-clock/random state consulted anywhere in this module."""
    iv_average, expected_move_points, expected_move_pct = derive_iv_and_expected_move(
        spot, strike, t_years, ce_premium, pe_premium, risk_free_rate,
    )
    realized_vol = derive_realized_vol(closes_with_ts)
    iv_state, iv_reason = derive_iv_state(iv_average, realized_vol)
    expected_move_state = derive_expected_move_state(expected_move_pct)
    volatility_regime, expansion_state, compression_state, _rv_for_regime = derive_regime_expansion_compression(closes_with_ts)
    skew_state = taxonomy.SKEW_UNKNOWN
    term_structure_state = taxonomy.TERM_STRUCTURE_UNKNOWN

    confidence = compute_confidence(iv_state, volatility_regime, iv_average is not None, volatility_regime != taxonomy.REGIME_UNKNOWN)

    assessment_id = _assessment_id(
        volatility_regime, iv_state, expected_move_state, skew_state, term_structure_state,
        expansion_state, compression_state, confidence, schema_version,
    )

    explanation = build_explanation(
        assessment_id=assessment_id, iv_state=iv_state, volatility_regime=volatility_regime,
        expected_move_state=expected_move_state, expansion_state=expansion_state,
        compression_state=compression_state, iv_reason=iv_reason,
    )

    return VolatilityStructureAssessment(
        assessment_id=assessment_id, timestamp=timestamp, volatility_regime=volatility_regime,
        iv_state=iv_state, expected_move_state=expected_move_state, skew_state=skew_state,
        term_structure_state=term_structure_state, expansion_state=expansion_state,
        compression_state=compression_state, confidence=confidence,
        iv_average=round(iv_average, 4) if iv_average is not None else None,
        realized_vol=round(realized_vol, 4) if realized_vol is not None else None,
        expected_move_pct=round(expected_move_pct, 3) if expected_move_pct is not None else None,
        explanation=explanation, provenance=provenance, schema_version=schema_version,
    )
