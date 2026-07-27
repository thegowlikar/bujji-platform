"""Performance Analytics & Edge Validation serialization — Series 101.
Pure dict round-trip, mirrors every prior MSI package's convention."""
from __future__ import annotations

from typing import Any, Dict

from .models import (
    CounterfactualReport, DecisionCategorySummary, EdgeValidationReport, MetricEstimate, TradeAnalytics,
)


def trade_analytics_to_dict(t: TradeAnalytics) -> Dict[str, Any]:
    return {
        "shadow_trade_id": t.shadow_trade_id, "realised_return_pct": t.realised_return_pct,
        "max_favourable_excursion": t.max_favourable_excursion, "max_adverse_excursion": t.max_adverse_excursion,
        "holding_period_days": t.holding_period_days, "realised_volatility": t.realised_volatility,
        "realised_direction": t.realised_direction, "exit_efficiency": t.exit_efficiency,
        "thesis_duration_days": t.thesis_duration_days, "lifecycle_duration_days": t.lifecycle_duration_days,
        "exit_reason": t.exit_reason, "realised_pnl": t.realised_pnl, "explanation": list(t.explanation),
        "provenance": t.provenance, "schema_version": t.schema_version,
    }


def metric_estimate_to_dict(m: MetricEstimate) -> Dict[str, Any]:
    return {
        "value": m.value, "sample_size": m.sample_size, "reliability": m.reliability.reliability,
        "reliability_reasoning": list(m.reliability.reasoning), "confidence_interval": m.confidence_interval,
    }


def edge_report_to_dict(r: EdgeValidationReport) -> Dict[str, Any]:
    return {
        "assessment_id": r.assessment_id, "timestamp": r.timestamp,
        "win_rate": metric_estimate_to_dict(r.win_rate), "expectancy": metric_estimate_to_dict(r.expectancy),
        "profit_factor": metric_estimate_to_dict(r.profit_factor),
        "average_winner": metric_estimate_to_dict(r.average_winner),
        "average_loser": metric_estimate_to_dict(r.average_loser),
        "median_winner": metric_estimate_to_dict(r.median_winner),
        "median_loser": metric_estimate_to_dict(r.median_loser),
        "payoff_ratio": metric_estimate_to_dict(r.payoff_ratio),
        "max_drawdown": metric_estimate_to_dict(r.max_drawdown),
        "longest_losing_streak": metric_estimate_to_dict(r.longest_losing_streak),
        "longest_winning_streak": metric_estimate_to_dict(r.longest_winning_streak),
        "risk_adjusted_return": metric_estimate_to_dict(r.risk_adjusted_return),
        "explanation": list(r.explanation), "provenance": r.provenance, "schema_version": r.schema_version,
    }


def category_summary_to_dict(s: DecisionCategorySummary) -> Dict[str, Any]:
    return {
        "category": s.category, "frequency": s.frequency, "average_confidence_rank": s.average_confidence_rank,
        "thesis_distribution": dict(s.thesis_distribution), "strategy_distribution": dict(s.strategy_distribution),
        "market_regime_distribution": dict(s.market_regime_distribution),
    }


def counterfactual_report_to_dict(r: CounterfactualReport) -> Dict[str, Any]:
    return {
        "assessment_id": r.assessment_id, "timestamp": r.timestamp, "total_non_approved": r.total_non_approved,
        "rejected_winners": r.rejected_winners, "rejected_losers": r.rejected_losers, "unknown": r.unknown,
        "records": [
            {"decision_id": rec.decision_id, "date": rec.date, "category": rec.category,
             "realised_movement_pct": rec.realised_movement_pct, "classification": rec.classification,
             "reasoning": list(rec.reasoning)}
            for rec in r.records
        ],
        "explanation": list(r.explanation), "provenance": r.provenance, "schema_version": r.schema_version,
    }
