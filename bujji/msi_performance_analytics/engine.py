"""Performance Analytics & Edge Validation engine — Series 101.

Every function here is PURE, DESCRIPTIVE statistics -- no tuning, no
optimisation, no machine learning, no data mining. Consumes ONLY real,
already-recorded `ShadowPosition` (Series 100) and `DecisionRecord`
(Series 99) objects. `bujji.intelligence.regime_brain.RegimeBrain`'s
own pure `_log_returns`/`_stdev` are reused directly for volatility,
exactly as Series 88/99 already do -- never re-derived.
"""
from __future__ import annotations

import hashlib
import math
from typing import Dict, Optional, Sequence, Tuple

from bujji.intelligence.regime_brain import RegimeBrain
from bujji.msi_decision_auditor.models import DecisionRecord

from . import config as _config
from . import taxonomy
from .models import (
    CounterfactualRecord, CounterfactualReport, DecisionCategorySummary, EdgeValidationReport,
    MetricEstimate, ReliabilityNote, TradeAnalytics,
)

_CONVICTION_RANK = {"NONE": 0, "LOW": 1, "MODERATE": 2, "HIGH": 3}


def _reliability(sample_size: int) -> ReliabilityNote:
    if sample_size >= _config.MIN_RELIABLE_SAMPLE_SIZE:
        return ReliabilityNote(
            sample_size=sample_size, reliability=taxonomy.RELIABILITY_RELIABLE,
            reasoning=(f"sample size {sample_size} meets the configured reliability floor "
                       f"({_config.MIN_RELIABLE_SAMPLE_SIZE})",),
        )
    return ReliabilityNote(
        sample_size=sample_size, reliability=taxonomy.RELIABILITY_NOT_RELIABLE,
        reasoning=(f"sample size {sample_size} is below the configured reliability floor "
                   f"({_config.MIN_RELIABLE_SAMPLE_SIZE}) -- this observation is NOT yet statistically reliable",),
    )


def _wilson_interval(successes: int, n: int, z: float = 1.96) -> Optional[Tuple[float, float]]:
    """Standard Wilson score interval for a binomial proportion -- pure
    math, no external statistics library, matching this codebase's
    established discipline (e.g. the hand-rolled binomial test used in
    the Series 86 predictive-value investigation)."""
    if n == 0:
        return None
    p = successes / n
    denom = 1 + z ** 2 / n
    centre = p + z ** 2 / (2 * n)
    margin = z * math.sqrt(p * (1 - p) / n + z ** 2 / (4 * n ** 2))
    lo = (centre - margin) / denom
    hi = (centre + margin) / denom
    return (round(max(0.0, lo), 4), round(min(1.0, hi), 4))


def _mean(values: Sequence[float]) -> Optional[float]:
    return round(sum(values) / len(values), 4) if values else None


def _median(values: Sequence[float]) -> Optional[float]:
    if not values:
        return None
    s = sorted(values)
    n = len(s)
    mid = n // 2
    return round(s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2, 4)


# ---------------------------------------------------------------------------
# Deliverable 2 -- Trade Analytics
# ---------------------------------------------------------------------------

def build_trade_analytics(
    position, daily_marks: Sequence[float], spot_at_entry: Optional[float], spot_at_exit: Optional[float],
) -> TradeAnalytics:
    """`daily_marks` is the real, day-by-day sequence of
    `unrealised_pnl` values already computed by Series 100's own
    `track_shadow_position` across the position's real holding period
    -- never re-derived, only aggregated here."""
    holding_period_days = len(daily_marks) if daily_marks else 0
    mfe = max(daily_marks) if daily_marks else None
    mae = min(daily_marks) if daily_marks else None
    realised_pnl = position.realised_pnl

    realised_return_pct = None
    if realised_pnl is not None and position.simulated_margin:
        realised_return_pct = round(realised_pnl / abs(position.simulated_margin) * 100.0, 4)

    realised_volatility = None
    if len(daily_marks) >= 3:
        returns = RegimeBrain._log_returns([abs(m) + 1.0 for m in daily_marks])  # +1 avoids log(0); real, disclosed shift.
        if len(returns) >= 2:
            realised_volatility = round(RegimeBrain._stdev(returns), 6)

    if spot_at_entry is not None and spot_at_exit is not None and spot_at_entry:
        move_pct = (spot_at_exit - spot_at_entry) / spot_at_entry * 100.0
        realised_direction = "UP" if move_pct > 0.01 else ("DOWN" if move_pct < -0.01 else "FLAT")
    else:
        realised_direction = "FLAT"

    exit_efficiency = None
    if realised_pnl is not None and mfe is not None and mfe > 0:
        exit_efficiency = round(realised_pnl / mfe, 4)

    explanation = (
        f"holding_period={holding_period_days} real day(s); MFE={mfe}, MAE={mae} from the real day-by-day "
        f"MTM series Series 100 already computed; realised_pnl={realised_pnl} (exit_reason={position.exit_reason})",
    )

    return TradeAnalytics(
        shadow_trade_id=position.shadow_trade_id, realised_return_pct=realised_return_pct,
        max_favourable_excursion=mfe, max_adverse_excursion=mae, holding_period_days=holding_period_days,
        realised_volatility=realised_volatility, realised_direction=realised_direction,
        exit_efficiency=exit_efficiency, thesis_duration_days=holding_period_days,
        lifecycle_duration_days=holding_period_days, exit_reason=position.exit_reason, realised_pnl=realised_pnl,
        explanation=explanation, provenance="bujji.msi_performance_analytics.engine.build_trade_analytics",
        schema_version=taxonomy.MSI_PERFORMANCE_ANALYTICS_VERSION,
    )


# ---------------------------------------------------------------------------
# Deliverable 3 -- Decision Analytics
# ---------------------------------------------------------------------------

def categorize_decision(decision: DecisionRecord) -> str:
    if decision.strategy_family is None:
        return taxonomy.CATEGORY_NO_TRADE
    if decision.decision_outcome == "TRADE_APPROVED":
        return taxonomy.CATEGORY_APPROVED
    return taxonomy.CATEGORY_REJECTED


def build_decision_category_summary(decisions: Sequence[DecisionRecord], category: str) -> DecisionCategorySummary:
    matching = [d for d in decisions if categorize_decision(d) == category]
    ranks = [_CONVICTION_RANK.get(d.confidence, 0) for d in matching]
    thesis_dist: Dict[str, int] = {}
    strategy_dist: Dict[str, int] = {}
    regime_dist: Dict[str, int] = {}
    for d in matching:
        thesis_dist[d.trade_thesis.thesis_type] = thesis_dist.get(d.trade_thesis.thesis_type, 0) + 1
        key = d.strategy_family or "NONE"
        strategy_dist[key] = strategy_dist.get(key, 0) + 1
        regime_dist[d.volatility_state] = regime_dist.get(d.volatility_state, 0) + 1
    return DecisionCategorySummary(
        category=category, frequency=len(matching),
        average_confidence_rank=_mean(ranks) if ranks else None,
        thesis_distribution=thesis_dist, strategy_distribution=strategy_dist, market_regime_distribution=regime_dist,
    )


# ---------------------------------------------------------------------------
# Deliverable 4 -- Edge Validation
# ---------------------------------------------------------------------------

def _drawdown_and_streaks(pnls: Sequence[float]) -> Tuple[float, int, int]:
    """Real, sequential (entry-order) max drawdown and longest win/loss
    streaks over the real, already-recorded P&L sequence."""
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    longest_win = longest_loss = cur_win = cur_loss = 0
    for pnl in pnls:
        equity += pnl
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
        if pnl > 0:
            cur_win += 1
            cur_loss = 0
        elif pnl < 0:
            cur_loss += 1
            cur_win = 0
        else:
            cur_win = cur_loss = 0
        longest_win = max(longest_win, cur_win)
        longest_loss = max(longest_loss, cur_loss)
    return round(max_dd, 2), longest_win, longest_loss


def build_edge_validation_report(trades: Sequence[TradeAnalytics], *, timestamp: str) -> EdgeValidationReport:
    """Deliverable 4. Every metric is paired with its own sample size
    and reliability note -- never presented alone."""
    n = len(trades)
    pnls = [t.realised_pnl for t in trades if t.realised_pnl is not None]
    winners = [p for p in pnls if p > 0]
    losers = [p for p in pnls if p <= 0]
    n_priced = len(pnls)

    win_rate_value = round(len(winners) / n_priced, 4) if n_priced else None
    win_rate_ci = _wilson_interval(len(winners), n_priced) if n_priced else None

    avg_win = _mean(winners)
    avg_loss = _mean(losers)
    expectancy_value = _mean(pnls)
    gross_win = sum(winners)
    gross_loss = abs(sum(losers))
    profit_factor_value = round(gross_win / gross_loss, 4) if gross_loss > 0 else (None if gross_win == 0 else float("inf"))
    payoff_ratio_value = round(abs(avg_win / avg_loss), 4) if avg_win is not None and avg_loss not in (None, 0) else None

    max_dd, longest_win_streak, longest_loss_streak = _drawdown_and_streaks(pnls) if pnls else (0.0, 0, 0)

    stdev_pnl = RegimeBrain._stdev(pnls) if len(pnls) >= 2 else None
    risk_adjusted_value = round(expectancy_value / stdev_pnl, 4) if expectancy_value is not None and stdev_pnl else None

    def est(value, sample_size, ci=None) -> MetricEstimate:
        return MetricEstimate(value=value, sample_size=sample_size, reliability=_reliability(sample_size), confidence_interval=ci)

    schema_version = taxonomy.MSI_PERFORMANCE_ANALYTICS_VERSION
    aid = hashlib.md5(f"{n}|{n_priced}|{schema_version}".encode("utf-8")).hexdigest()

    explanation = (
        f"computed over {n} total shadow trades ({n_priced} with a real realised P&L); "
        f"reliability floor is {_config.MIN_RELIABLE_SAMPLE_SIZE} observations -- every metric below "
        f"is explicitly flagged when the real sample does not meet it",
    )

    return EdgeValidationReport(
        assessment_id=aid, timestamp=timestamp,
        win_rate=est(win_rate_value, n_priced, win_rate_ci),
        expectancy=est(expectancy_value, n_priced),
        profit_factor=est(profit_factor_value, n_priced),
        average_winner=est(avg_win, len(winners)),
        average_loser=est(avg_loss, len(losers)),
        median_winner=est(_median(winners), len(winners)),
        median_loser=est(_median(losers), len(losers)),
        payoff_ratio=est(payoff_ratio_value, n_priced),
        max_drawdown=est(max_dd, n_priced),
        longest_losing_streak=est(float(longest_loss_streak), n_priced),
        longest_winning_streak=est(float(longest_win_streak), n_priced),
        risk_adjusted_return=est(risk_adjusted_value, n_priced),
        explanation=explanation, provenance="bujji.msi_performance_analytics.engine.build_edge_validation_report",
        schema_version=schema_version,
    )


# ---------------------------------------------------------------------------
# Deliverable 6 -- Counterfactual Analysis
# ---------------------------------------------------------------------------

def build_counterfactual_report(
    decisions: Sequence[DecisionRecord], outcomes_by_date: Dict[str, float], *, timestamp: str,
) -> CounterfactualReport:
    """For every REJECTED or NO_TRADE decision, classify ONLY against
    what actually happened (`outcomes_by_date`: real, already-recorded
    `OutcomeRecord.realised_movement_pct` keyed by date) -- never
    against what should have been decided. A positive real move is
    treated as a `REJECTED_WINNER` proxy ONLY when the day's real
    thesis had a directional lean matching the move's sign; a genuinely
    neutral/range thesis with a real move is not judged as a missed
    directional win. This is a DESCRIPTIVE classification, not a
    recommendation."""
    records = []
    for d in decisions:
        category = categorize_decision(d)
        if category == taxonomy.CATEGORY_APPROVED:
            continue
        movement = outcomes_by_date.get(d.date)
        if movement is None:
            records.append(CounterfactualRecord(
                decision_id=d.decision_id, date=d.date, category=category, realised_movement_pct=None,
                classification=taxonomy.COUNTERFACTUAL_UNKNOWN, reasoning=("no real outcome data available for this day",),
            ))
            continue
        bullish = d.market_direction in ("STRONG_BULLISH", "BULLISH", "WEAK_BULLISH")
        bearish = d.market_direction in ("STRONG_BEARISH", "BEARISH", "WEAK_BEARISH")
        would_have_profited = (bullish and movement > 0) or (bearish and movement < 0)
        if d.market_direction in ("NEUTRAL", "MIXED", "UNKNOWN"):
            classification = taxonomy.COUNTERFACTUAL_UNKNOWN
            reasoning = (f"market_direction={d.market_direction} on this day -- no directional lean to "
                        f"judge the real {movement}% move against",)
        elif would_have_profited:
            classification = taxonomy.COUNTERFACTUAL_REJECTED_WINNER
            reasoning = (f"market_direction={d.market_direction} agreed with the real {movement}% move",)
        else:
            classification = taxonomy.COUNTERFACTUAL_REJECTED_LOSER
            reasoning = (f"market_direction={d.market_direction} disagreed with the real {movement}% move",)
        records.append(CounterfactualRecord(
            decision_id=d.decision_id, date=d.date, category=category, realised_movement_pct=movement,
            classification=classification, reasoning=reasoning,
        ))

    rejected_winners = sum(1 for r in records if r.classification == taxonomy.COUNTERFACTUAL_REJECTED_WINNER)
    rejected_losers = sum(1 for r in records if r.classification == taxonomy.COUNTERFACTUAL_REJECTED_LOSER)
    unknown = sum(1 for r in records if r.classification == taxonomy.COUNTERFACTUAL_UNKNOWN)

    schema_version = taxonomy.MSI_PERFORMANCE_ANALYTICS_VERSION
    aid = hashlib.md5(f"{len(records)}|{rejected_winners}|{rejected_losers}|{schema_version}".encode("utf-8")).hexdigest()
    explanation = (
        f"{len(records)} non-approved decisions evaluated against real, already-recorded outcomes; "
        f"{unknown} could not be classified (no directional lean or no outcome data) -- this evidence is "
        f"descriptive only and does not by itself justify changing the decision engine",
    )

    return CounterfactualReport(
        assessment_id=aid, timestamp=timestamp, total_non_approved=len(records),
        rejected_winners=rejected_winners, rejected_losers=rejected_losers, unknown=unknown,
        records=tuple(records), explanation=explanation,
        provenance="bujji.msi_performance_analytics.engine.build_counterfactual_report", schema_version=schema_version,
    )
