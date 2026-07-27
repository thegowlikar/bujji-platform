"""Deliverable 7 -- End-of-day report."""
from __future__ import annotations

from collections import Counter
from typing import Optional, Sequence

from ..live_pipeline_bridge import SessionResult
from ..live_shadow_validation import FullCadenceResult, OperationalMetrics


def build_end_of_day_report(
    day: str, result: SessionResult, cadence_results: Sequence[FullCadenceResult],
    metrics: OperationalMetrics, *, reconnect_count: int,
    replay_parity_pct: Optional[float] = None,
) -> "DailyOutcome":
    from .operator import DailyOutcome  # local import: avoids a circular import with operator.py

    decisions = [c.decision for c in cadence_results]
    no_trade = sum(1 for d in decisions if d.decision_outcome != "TRADE_APPROVED")
    strategy_dist = Counter(c.selection.selected_strategy_family or "NONE" for c in cadence_results)
    thesis_dist = Counter(d.trade_thesis.thesis_type for d in decisions)

    shadow_positions = [c.shadow_position for c in cadence_results if c.shadow_position is not None]
    virtual_pnl = sum(
        (p.realised_pnl if p.completed and p.realised_pnl is not None
         else (p.unrealised_pnl or 0.0))
        for p in shadow_positions
    )

    warnings, failures = [], []
    if metrics.dropped_ticks:
        warnings.append(f"{metrics.dropped_ticks} duplicate tick(s) dropped")
    if metrics.duplicate_observations:
        warnings.append(f"{metrics.duplicate_observations} duplicate observation event(s) detected")
    if reconnect_count:
        warnings.append(f"{reconnect_count} websocket reconnect(s) occurred")
    if replay_parity_pct is not None and replay_parity_pct < 0.99:
        warnings.append(f"replay parity {replay_parity_pct*100:.1f}% below the 99% threshold (see docs)")

    outcome = DailyOutcome(
        day=day, decisions=len(decisions), no_trade_count=no_trade,
        strategy_distribution=dict(strategy_dist), thesis_distribution=dict(thesis_dist),
        virtual_pnl=virtual_pnl, metrics=metrics, cadence_results=list(cadence_results),
        warnings=warnings, failures=failures,
    )
    outcome.report_text = render_report_text(outcome, replay_parity_pct=replay_parity_pct)
    return outcome


def render_report_text(outcome, *, replay_parity_pct: Optional[float] = None) -> str:
    lines = [
        f"BUJJI Live Shadow -- End of Day Report -- {outcome.day}",
        "",
        "Market summary",
        f"  Decisions:              {outcome.decisions}",
        f"  No-trade count:         {outcome.no_trade_count}",
        "",
        "Strategy distribution:",
    ]
    for k, v in sorted(outcome.strategy_distribution.items()):
        lines.append(f"  {k:24s} {v}")
    lines.append("")
    lines.append("Thesis distribution:")
    for k, v in sorted(outcome.thesis_distribution.items()):
        lines.append(f"  {k:24s} {v}")
    lines.append("")
    lines.append(f"Virtual P&L (shadow positions only, never real capital): {outcome.virtual_pnl:,.2f}")
    lines.append("")
    lines.append(f"Replay parity: {'N/A (no replay reference supplied)' if replay_parity_pct is None else f'{replay_parity_pct*100:.1f}%'}")
    lines.append("")
    lines.append("System health")
    if outcome.metrics:
        lines.append(f"  reconnects:              {outcome.metrics.reconnects}")
        lines.append(f"  dropped_ticks:           {outcome.metrics.dropped_ticks}")
        lines.append(f"  duplicate_observations:  {outcome.metrics.duplicate_observations}")
        lines.append(f"  peak_memory_kb:          {outcome.metrics.peak_memory_kb}")
    lines.append("")
    lines.append("Warnings:")
    lines.extend(f"  - {w}" for w in outcome.warnings) if outcome.warnings else lines.append("  none")
    lines.append("Failures:")
    lines.extend(f"  - {f}" for f in outcome.failures) if outcome.failures else lines.append("  none")
    return "\n".join(lines)
