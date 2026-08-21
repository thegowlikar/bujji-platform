"""bujji.microstructure_intelligence.classifier — Phase 20.27.

Turns a chronological list of `bujji.market_microstructure.models.
MinuteObservation` into one `MicrostructureReading` -- the missing
interpretation step between "here is the 1-minute texture" and "what
does that texture mean." No ML, no optimization: deterministic
threshold rules, same explainable-over-clever discipline as every
`bujji.intelligence.*` brain (`RegimeBrain`, `StructureBrain`, ...).

DESIGN PRINCIPLE: pure function of its `observations` argument only.
No broker import, no wall-clock read, no `IntelligenceContext`
dependency -- `window_start`/`window_end`/`session_date` are derived
from the observations themselves. This means the classifier structurally
cannot see "future" data: it only ever knows what is in the list it was
given, and calling it twice with the same list is guaranteed to return
the same reading (see tests: determinism, no future data leakage).

METHOD
------
Split the input observations into two halves (oldest half, newest
half) -- the SAME split-in-half technique `RegimeBrain._compression_ratio`
already uses for its own first-half-vs-second-half comparison (method
reused by design, not by code -- `MinuteObservation` and `Candle` are
different types, so there is nothing to import). For each half:

  * avg_tick_count        -- mean `tick_count` (activity/density)
  * avg_range_pct         -- mean (high-low)/close*100 (movement, price-
                              level-independent so the same thresholds
                              work for spot, an option premium, or VIX)
  * closes                -- used for a within-half efficiency ratio
                              (Kaufman ER, the same simple measure
                              `RegimeBrain._efficiency_ratio` uses --
                              again the same technique, not the same code)

`density_ratio` = second half / first half avg_tick_count.
`range_ratio`   = second half / first half avg_range_pct.
`second_half_efficiency_ratio` = |net move| / path length within the
second half's closes -- 0 (pure noise) to 1 (a straight line).

Classification priority (checked in this order -- documented so the
"why this label and not another" question always has one answer,
same discipline as `RegimeBrain._classify`):

    1. Insufficient observations       -> UNKNOWN  (DataQuality.INSUFFICIENT)
    2. Mixed instruments in one call   -> UNKNOWN  (DataQuality.INSUFFICIENT, caller error, fails closed)
    3. Abnormal silence / irregular tick cadence -> LIQUIDITY_STRESS
    4. A real directional move that mostly reverses within the window -> FALSE_BREAKOUT
    5. Rising/high activity with flat-or-shrinking range -> ABSORPTION
    6. Rising activity + rising range + clear direction -> EXPANSION
    7. Shrinking range + shrinking-or-flat activity -> CONTRACTION
    8. Low absolute activity and movement (not just relative to itself) -> QUIET
    9. Otherwise -> NORMAL

CALIBRATION NOTE (same discipline as every threshold constant in this
codebase, e.g. `RegimeBrain`'s ER/vol thresholds): every constant below
is a documented first pass, not a statistically calibrated conclusion --
there is not yet enough real 1-minute microstructure history to
calibrate them (this package has never been fed a live tick, see the
Phase 20.27 audit). Revisit once real data accumulates (Phase 20.28+).
"""
from __future__ import annotations

from typing import List, Optional, Tuple

from bujji.market_microstructure.models import MinuteObservation

from .models import DataQuality, MicrostructureReading, MicrostructureState

MIN_OBSERVATIONS = 6  # mirrors RegimeBrain.MIN_CANDLES -- two halves of >= 3 each.

# -- Liquidity stress ---------------------------------------------------
# Distinct from `microstructure_aggregator.SILENCE_TOLERANCE_SECONDS`
# (20.0s) on purpose: that constant drives a per-window DATA-QUALITY
# score; this one drives a MARKET-CONDITION classification, a related
# but different question, so it is not reused verbatim.
SILENCE_STRESS_SECONDS = 30.0
TICK_COUNT_CV_STRESS_THRESHOLD = 0.75  # stdev/mean of tick_count across the window

# -- False breakout -------------------------------------------------------
BREAKOUT_MOVE_PCT_THRESHOLD = 0.05     # first-half move, as % of its opening price
RETRACEMENT_RATIO_THRESHOLD = 0.6      # fraction of that move undone in the second half

# -- Absorption / Expansion / Contraction (ratios of second half : first half) --
ABSORPTION_DENSITY_RATIO_THRESHOLD = 1.3
ABSORPTION_RANGE_RATIO_THRESHOLD = 0.9
EXPANSION_DENSITY_RATIO_THRESHOLD = 1.3
EXPANSION_RANGE_RATIO_THRESHOLD = 1.3
EXPANSION_ER_THRESHOLD = 0.5
CONTRACTION_RANGE_RATIO_THRESHOLD = 0.7
CONTRACTION_DENSITY_RATIO_THRESHOLD = 0.85

# -- Quiet (absolute floors, not relative-to-self) -------------------------
QUIET_TICK_DENSITY_THRESHOLD = 3.0     # avg ticks/minute
QUIET_RANGE_PCT_THRESHOLD = 0.02       # avg (high-low)/close * 100 per minute


class MicrostructureClassifier:
    """Stateless: call `analyze(observations)` with one instrument's
    chronological `MinuteObservation` history. Never mutates anything,
    never talks to a broker, never decides whether to trade -- purely
    an observation, same first principle as every `bujji.intelligence.*`
    brain."""

    def analyze(self, observations: List[MinuteObservation]) -> MicrostructureReading:
        observations = sorted(observations, key=lambda o: o.window_start)
        n = len(observations)

        if n < MIN_OBSERVATIONS:
            return self._insufficient(
                observations, f"insufficient_data: {n} observation(s), need >= {MIN_OBSERVATIONS}",
            )

        instruments = {o.instrument for o in observations}
        if len(instruments) > 1:
            return self._insufficient(
                observations,
                f"mixed_instruments: expected one instrument, got {sorted(instruments)}",
            )

        mid = n // 2
        first_half, second_half = observations[:mid], observations[mid:]

        first_density = self._avg_tick_count(first_half)
        second_density = self._avg_tick_count(second_half)
        first_range_pct = self._avg_range_pct(first_half)
        second_range_pct = self._avg_range_pct(second_half)

        density_ratio = self._ratio(second_density, first_density)
        range_ratio = self._ratio(second_range_pct, first_range_pct)

        second_closes = [o.close for o in second_half]
        second_half_er, second_half_net_move, second_half_path = self._efficiency_ratio(second_closes)

        first_closes = [o.close for o in first_half]
        _, first_half_net_move, _ = self._efficiency_ratio(first_closes)

        max_silence = self._max_silence(observations)
        tick_count_cv = self._coefficient_of_variation([o.tick_count for o in observations])

        overall_density = self._avg_tick_count(observations)
        overall_range_pct = self._avg_range_pct(observations)

        metrics = {
            "observations_used": n,
            "avg_tick_count_prior": round(first_density, 4),
            "avg_tick_count_recent": round(second_density, 4),
            "density_ratio": round(density_ratio, 4) if density_ratio is not None else None,
            "avg_range_pct_prior": round(first_range_pct, 6),
            "avg_range_pct_recent": round(second_range_pct, 6),
            "range_ratio": round(range_ratio, 4) if range_ratio is not None else None,
            "second_half_efficiency_ratio": round(second_half_er, 4),
            "first_half_net_move": round(first_half_net_move, 4),
            "second_half_net_move": round(second_half_net_move, 4),
            "max_tick_silence_seconds": round(max_silence, 3) if max_silence is not None else None,
            "tick_count_coefficient_of_variation": round(tick_count_cv, 4) if tick_count_cv is not None else None,
            "overall_avg_tick_count": round(overall_density, 4),
            "overall_avg_range_pct": round(overall_range_pct, 6),
        }

        state, confidence, reasons = self._classify(
            density_ratio=density_ratio, range_ratio=range_ratio,
            second_half_er=second_half_er,
            first_half_net_move=first_half_net_move, second_half_net_move=second_half_net_move,
            first_half_open=first_half[0].open,
            max_silence=max_silence, tick_count_cv=tick_count_cv,
            overall_density=overall_density, overall_range_pct=overall_range_pct,
        )

        return MicrostructureReading(
            instrument=observations[0].instrument,
            session_date=observations[-1].session_date,
            state=state, confidence=confidence, data_quality=DataQuality.SUFFICIENT,
            reasons=reasons, supporting_metrics=metrics, observations_used=n,
            window_start=observations[0].window_start, window_end=observations[-1].window_end,
        )

    # ------------------------------------------------------------------ #
    # Metrics (each independently testable, each documented above)
    # ------------------------------------------------------------------ #
    @staticmethod
    def _avg_tick_count(obs: List[MinuteObservation]) -> float:
        return sum(o.tick_count for o in obs) / len(obs) if obs else 0.0

    @staticmethod
    def _avg_range_pct(obs: List[MinuteObservation]) -> float:
        pct = [((o.high - o.low) / o.close * 100.0) for o in obs if o.close > 0]
        return sum(pct) / len(pct) if pct else 0.0

    @staticmethod
    def _ratio(recent: float, prior: float) -> Optional[float]:
        if prior <= 0:
            return None
        return recent / prior

    @staticmethod
    def _efficiency_ratio(closes: List[float]) -> Tuple[float, float, float]:
        """Kaufman efficiency ratio -- same simple measure `RegimeBrain`
        uses on candle closes, applied here to `MinuteObservation`
        closes. Returns (er, net_move, path_length)."""
        if len(closes) < 2:
            return 0.0, 0.0, 0.0
        net_move = closes[-1] - closes[0]
        path_length = sum(abs(closes[i] - closes[i - 1]) for i in range(1, len(closes)))
        er = abs(net_move) / path_length if path_length > 0 else 0.0
        return er, net_move, path_length

    @staticmethod
    def _max_silence(obs: List[MinuteObservation]) -> Optional[float]:
        gaps = [o.max_tick_silence_seconds for o in obs if o.max_tick_silence_seconds is not None]
        return max(gaps) if gaps else None

    @staticmethod
    def _coefficient_of_variation(values: List[int]) -> Optional[float]:
        if len(values) < 2:
            return None
        mean = sum(values) / len(values)
        if mean <= 0:
            return None
        variance = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
        return (variance ** 0.5) / mean

    # ------------------------------------------------------------------ #
    # Classification (priority order documented in the module docstring)
    # ------------------------------------------------------------------ #
    def _classify(
        self, *, density_ratio: Optional[float], range_ratio: Optional[float],
        second_half_er: float, first_half_net_move: float, second_half_net_move: float,
        first_half_open: float, max_silence: Optional[float], tick_count_cv: Optional[float],
        overall_density: float, overall_range_pct: float,
    ) -> Tuple[MicrostructureState, float, Tuple[str, ...]]:

        if (max_silence is not None and max_silence >= SILENCE_STRESS_SECONDS) or (
            tick_count_cv is not None and tick_count_cv >= TICK_COUNT_CV_STRESS_THRESHOLD
        ):
            reasons = []
            if max_silence is not None and max_silence >= SILENCE_STRESS_SECONDS:
                reasons.append(f"max_tick_silence_seconds {max_silence:.1f} >= threshold {SILENCE_STRESS_SECONDS}")
            if tick_count_cv is not None and tick_count_cv >= TICK_COUNT_CV_STRESS_THRESHOLD:
                reasons.append(f"tick_count_cv {tick_count_cv:.3f} >= threshold {TICK_COUNT_CV_STRESS_THRESHOLD}")
            confidence = self._scaled_confidence(
                max(max_silence or 0.0, (tick_count_cv or 0.0) * SILENCE_STRESS_SECONDS),
                SILENCE_STRESS_SECONDS, SILENCE_STRESS_SECONDS * 2,
            )
            return MicrostructureState.LIQUIDITY_STRESS, confidence, tuple(reasons)

        breakout_move_pct = (abs(first_half_net_move) / first_half_open * 100.0) if first_half_open > 0 else 0.0
        opposite_direction = (
            first_half_net_move != 0
            and second_half_net_move != 0
            and (first_half_net_move > 0) != (second_half_net_move > 0)
        )
        retracement_ratio = (
            abs(second_half_net_move) / abs(first_half_net_move) if opposite_direction and first_half_net_move != 0 else None
        )
        if (
            breakout_move_pct >= BREAKOUT_MOVE_PCT_THRESHOLD
            and retracement_ratio is not None
            and retracement_ratio >= RETRACEMENT_RATIO_THRESHOLD
        ):
            confidence = self._scaled_confidence(retracement_ratio, RETRACEMENT_RATIO_THRESHOLD, 1.0)
            return MicrostructureState.FALSE_BREAKOUT, confidence, (
                f"first-half move {breakout_move_pct:.3f}% >= threshold {BREAKOUT_MOVE_PCT_THRESHOLD}%",
                f"retracement_ratio {retracement_ratio:.3f} >= threshold {RETRACEMENT_RATIO_THRESHOLD}",
            )

        if (
            density_ratio is not None and density_ratio >= ABSORPTION_DENSITY_RATIO_THRESHOLD
            and range_ratio is not None and range_ratio <= ABSORPTION_RANGE_RATIO_THRESHOLD
        ):
            confidence = self._scaled_confidence(density_ratio, ABSORPTION_DENSITY_RATIO_THRESHOLD, ABSORPTION_DENSITY_RATIO_THRESHOLD * 2)
            return MicrostructureState.ABSORPTION, confidence, (
                f"density_ratio {density_ratio:.3f} >= threshold {ABSORPTION_DENSITY_RATIO_THRESHOLD}",
                f"range_ratio {range_ratio:.3f} <= threshold {ABSORPTION_RANGE_RATIO_THRESHOLD}",
            )

        if (
            density_ratio is not None and density_ratio >= EXPANSION_DENSITY_RATIO_THRESHOLD
            and range_ratio is not None and range_ratio >= EXPANSION_RANGE_RATIO_THRESHOLD
            and second_half_er >= EXPANSION_ER_THRESHOLD
        ):
            confidence = self._scaled_confidence(second_half_er, EXPANSION_ER_THRESHOLD, 1.0)
            return MicrostructureState.EXPANSION, confidence, (
                f"density_ratio {density_ratio:.3f} >= threshold {EXPANSION_DENSITY_RATIO_THRESHOLD}",
                f"range_ratio {range_ratio:.3f} >= threshold {EXPANSION_RANGE_RATIO_THRESHOLD}",
                f"second_half_efficiency_ratio {second_half_er:.3f} >= threshold {EXPANSION_ER_THRESHOLD}",
            )

        if (
            range_ratio is not None and range_ratio <= CONTRACTION_RANGE_RATIO_THRESHOLD
            and density_ratio is not None and density_ratio <= CONTRACTION_DENSITY_RATIO_THRESHOLD
        ):
            confidence = self._scaled_confidence(
                CONTRACTION_RANGE_RATIO_THRESHOLD - range_ratio, 0.0, CONTRACTION_RANGE_RATIO_THRESHOLD,
            )
            return MicrostructureState.CONTRACTION, confidence, (
                f"range_ratio {range_ratio:.3f} <= threshold {CONTRACTION_RANGE_RATIO_THRESHOLD}",
                f"density_ratio {density_ratio:.3f} <= threshold {CONTRACTION_DENSITY_RATIO_THRESHOLD}",
            )

        if overall_density <= QUIET_TICK_DENSITY_THRESHOLD and overall_range_pct <= QUIET_RANGE_PCT_THRESHOLD:
            confidence = self._scaled_confidence(
                QUIET_TICK_DENSITY_THRESHOLD - overall_density, 0.0, QUIET_TICK_DENSITY_THRESHOLD,
            )
            return MicrostructureState.QUIET, confidence, (
                f"overall_avg_tick_count {overall_density:.3f} <= threshold {QUIET_TICK_DENSITY_THRESHOLD}",
                f"overall_avg_range_pct {overall_range_pct:.4f} <= threshold {QUIET_RANGE_PCT_THRESHOLD}",
            )

        return MicrostructureState.NORMAL, 0.5, (
            "none of QUIET/EXPANSION/CONTRACTION/ABSORPTION/LIQUIDITY_STRESS/FALSE_BREAKOUT thresholds crossed",
        )

    @staticmethod
    def _scaled_confidence(distance_past_threshold: float, low: float, high: float) -> float:
        """Confidence rises from 0.5 at the threshold boundary itself to
        1.0 at (or past) `high` -- same shape as `RegimeBrain.
        _scaled_confidence`, reused as a technique (this is a private,
        4-line static method, not worth importing across packages)."""
        if high <= low:
            return 0.5
        span = high - low
        scaled = min(1.0, max(0.0, distance_past_threshold / span))
        return round(0.5 + 0.5 * scaled, 4)

    @staticmethod
    def _insufficient(observations: List[MinuteObservation], reason: str) -> MicrostructureReading:
        instruments = {o.instrument for o in observations}
        instrument = next(iter(instruments)) if len(instruments) == 1 else None
        return MicrostructureReading(
            instrument=instrument,
            session_date=observations[-1].session_date if observations else None,
            state=MicrostructureState.UNKNOWN, confidence=0.0,
            data_quality=DataQuality.INSUFFICIENT, reasons=(reason,),
            supporting_metrics={}, observations_used=len(observations),
            window_start=observations[0].window_start if observations else None,
            window_end=observations[-1].window_end if observations else None,
        )
