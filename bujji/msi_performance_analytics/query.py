"""Performance Analytics & Edge Validation query helpers — Series 101.
Deliverable 5's root-cause breakdowns AND Deliverable 8's dashboard
views live here -- ALL of them descriptive only: counts, means, and
real recorded fields grouped by a dimension. No optimisation, no
threshold search, no parameter fitting anywhere in this file."""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Callable, Dict, Optional, Sequence, Tuple

from . import config as _config
from .engine import _mean, _reliability
from .models import MetricEstimate, TradeAnalytics


def _pnl_by(trades: Sequence, positions_by_id: Dict[str, object], key_fn: Callable) -> Dict[str, MetricEstimate]:
    """Deliverable 5: group real realised P&L by an arbitrary dimension
    (thesis / strategy family / direction / ... ), reporting a plain
    mean P&L per group, each with its own real sample size -- purely
    descriptive, never a ranking or a recommendation."""
    groups: Dict[str, list] = defaultdict(list)
    for t in trades:
        pos = positions_by_id.get(t.shadow_trade_id)
        if pos is None:
            continue
        key = key_fn(pos, t)
        if key is None:
            continue
        if t.realised_pnl is not None:
            groups[key].append(t.realised_pnl)
    return {
        key: MetricEstimate(value=_mean(pnls), sample_size=len(pnls), reliability=_reliability(len(pnls)), confidence_interval=None)
        for key, pnls in groups.items()
    }


def root_cause_by_strategy_family(trades: Sequence[TradeAnalytics], positions_by_id: Dict[str, object]) -> Dict[str, MetricEstimate]:
    return _pnl_by(trades, positions_by_id, lambda pos, t: pos.entry_structure.split(" (")[0])


def root_cause_by_construction_type(trades: Sequence[TradeAnalytics], positions_by_id: Dict[str, object]) -> Dict[str, MetricEstimate]:
    def key_fn(pos, t):
        parts = pos.entry_structure.split(" (")
        return parts[1].rstrip(")") if len(parts) > 1 else None
    return _pnl_by(trades, positions_by_id, key_fn)


def root_cause_by_direction(trades: Sequence[TradeAnalytics], positions_by_id: Dict[str, object]) -> Dict[str, MetricEstimate]:
    return _pnl_by(trades, positions_by_id, lambda pos, t: t.realised_direction)


def root_cause_by_thesis(trades: Sequence[TradeAnalytics], thesis_by_id: Dict[str, str]) -> Dict[str, MetricEstimate]:
    groups: Dict[str, list] = defaultdict(list)
    for t in trades:
        thesis_type = thesis_by_id.get(t.shadow_trade_id)
        if thesis_type is not None and t.realised_pnl is not None:
            groups[thesis_type].append(t.realised_pnl)
    return {
        key: MetricEstimate(value=_mean(pnls), sample_size=len(pnls), reliability=_reliability(len(pnls)), confidence_interval=None)
        for key, pnls in groups.items()
    }


def root_cause_by_confidence_band(trades: Sequence[TradeAnalytics], confidence_by_id: Dict[str, str]) -> Dict[str, MetricEstimate]:
    groups: Dict[str, list] = defaultdict(list)
    for t in trades:
        band = confidence_by_id.get(t.shadow_trade_id)
        if band is not None and t.realised_pnl is not None:
            groups[band].append(t.realised_pnl)
    return {
        key: MetricEstimate(value=_mean(pnls), sample_size=len(pnls), reliability=_reliability(len(pnls)), confidence_interval=None)
        for key, pnls in groups.items()
    }


# --- Deliverable 8: dashboard views ----------------------------------------

def _fmt_metric(name: str, m: MetricEstimate, as_pct: bool = False, as_currency: bool = False) -> str:
    if m.value is None:
        val_str = "unavailable"
    elif as_pct:
        val_str = f"{m.value * 100:.1f}%"
    elif as_currency:
        val_str = f"₹{m.value:,.2f}"
    else:
        val_str = f"{m.value}"
    reliability_note = "" if m.reliability.reliability == "RELIABLE" else f"  [{m.reliability.reliability}]"
    return f"{name}\n  {val_str} (n={m.sample_size}){reliability_note}"


def overall_dashboard(total_trades: int, wins: int, losses: int, edge_report) -> str:
    lines = [
        "Trades", f"  {total_trades}", "", "Win", f"  {wins}", "", "Loss", f"  {losses}", "",
        _fmt_metric("Expectancy", edge_report.expectancy, as_currency=True), "",
        _fmt_metric("Win Rate", edge_report.win_rate, as_pct=True), "",
        _fmt_metric("Profit Factor", edge_report.profit_factor), "",
        _fmt_metric("Max Drawdown", edge_report.max_drawdown, as_currency=True),
    ]
    return "\n".join(lines)


def breakdown_view(title: str, breakdown: Dict[str, MetricEstimate]) -> str:
    lines = [title, ""]
    for key, m in sorted(breakdown.items(), key=lambda kv: kv[0]):
        lines.append(_fmt_metric(f"  {key}", m, as_currency=True))
    return "\n".join(lines)
