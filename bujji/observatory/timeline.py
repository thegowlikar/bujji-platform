"""Market Intelligence Observatory — Historical Reasoning Timeline &
Trading Impact.

BUJJI Options OS v3, Engineering Series 66.

Read-only. Builds the Evidence -> MIC classifications -> Trading Brain
interpretation -> Strategy selection -> Runtime outcome timeline for
one already-recorded session, and an explanation of trading impact
when comparing two sessions. Every step is read verbatim from an
already-recorded session dict / evidence list -- nothing here
recomputes any stage.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Sequence, Tuple

from .comparison import CANONICAL_FIELD_ORDER, SessionDiff

TIMELINE_STAGES: Tuple[str, ...] = (
    "evidence",
    "mic_classification",
    "trading_brain_interpretation",
    "strategy_selection",
    "runtime_outcome",
)


@dataclass(frozen=True)
class TimelineStep:
    stage: str
    summary: str
    detail: Dict[str, Any]


@dataclass(frozen=True)
class ReasoningTimeline:
    session_id: str
    steps: Tuple[TimelineStep, ...]


def build_timeline(session: Dict[str, Any], evidence: Sequence[Dict[str, Any]] = ()) -> ReasoningTimeline:
    """Build the reasoning timeline for one already-recorded session.
    Never recomputes any stage -- every field read here is copied
    verbatim from `session`/`evidence`.
    """
    steps = []

    steps.append(
        TimelineStep(
            stage="evidence",
            summary=f"{len(evidence)} Evidence record(s) from MIC v2's analyzer modules." if evidence
            else "No Evidence records supplied for this session -- not recorded, never fabricated.",
            detail={"evidence": list(evidence)},
        )
    )

    mic_fields = {
        k: session.get(k)
        for k in ("market_context", "market_opinion", "context_stability", "calibration", "governance", "lifecycle", "contract")
        if k in session
    }
    steps.append(
        TimelineStep(
            stage="mic_classification",
            summary=", ".join(f"{k}={v}" for k, v in mic_fields.items()) or "No MIC classification recorded.",
            detail=mic_fields,
        )
    )

    steps.append(
        TimelineStep(
            stage="trading_brain_interpretation",
            summary=(
                f"Strategy Selector evaluated market_context={mic_fields.get('market_context', 'UNKNOWN')}"
                f" / market_opinion={mic_fields.get('market_opinion', 'UNKNOWN')}."
            ),
            detail={"market_context": mic_fields.get("market_context"), "market_opinion": mic_fields.get("market_opinion")},
        )
    )

    strategy = session.get("strategy")
    steps.append(
        TimelineStep(
            stage="strategy_selection",
            summary=f"Selected: {strategy}" if strategy else "NO_STRATEGY -- Strategy Selector declined.",
            detail={"strategy": strategy},
        )
    )

    outcome = session.get("outcome")
    steps.append(
        TimelineStep(
            stage="runtime_outcome",
            summary=f"Runtime outcome: {outcome}" if outcome else "No runtime outcome recorded.",
            detail={"outcome": outcome, "governance": session.get("governance")},
        )
    )

    return ReasoningTimeline(session_id=session.get("id", "UNKNOWN"), steps=tuple(steps))


def explain_trading_impact(
    diff: SessionDiff,
    session_a: Optional[Dict[str, Any]] = None,
    session_b: Optional[Dict[str, Any]] = None,
) -> str:
    """Turn a `SessionDiff` (comparison.py) into a divergence-chain
    narrative: the first field (in CANONICAL_FIELD_ORDER order) that
    differs between the two sessions, followed by every other changed
    field (also in canonical order) as a "->" consequence line. This
    makes no causal claim beyond canonical-order sequencing -- it never
    asserts that the first-divergence field caused any later change,
    only that it differs first in the documented composition chain.
    Built only from `diff`'s own already-computed fields.

    `session_a`/`session_b` are the original (optional) raw session
    dicts the diff was built from. When supplied and `context_stability`
    is among the changed fields, this appends a SUPPLEMENTARY block
    (clearly labeled, not part of the ordered divergence chain) showing
    the before/after `stability_reasoning_summary` and `transition_events`
    values -- these two fields are deliberately excluded from
    CANONICAL_FIELD_ORDER (see comparison.py) since one is free text and
    the other is list-valued, but `stability_reasoning_summary` in
    particular explains *why* `context_stability` changed (e.g.
    "transition_rate=0.267 at or below 0.6"), so it is surfaced here as
    non-canonical narrative context rather than folded into the
    equality-diff chain above.
    """
    if not diff.changed_fields:
        return f"{diff.session_id}: no change between the two replay runs."

    first = diff.first_divergence
    lines = [f"{diff.session_id}:", f"First divergence: {first}"]

    if first is not None and first in diff.changed_fields:
        vals = diff.changed_fields[first]
        lines.append(f"  {vals['before']} -> {vals['after']}")

    for field_name in CANONICAL_FIELD_ORDER:
        if field_name == first:
            continue
        if field_name in diff.changed_fields:
            vals = diff.changed_fields[field_name]
            lines.append(f"  -> {field_name} changed: {vals['before']} -> {vals['after']}")

    lines.append(f"  downstream_impact: {diff.downstream_impact}")

    if "context_stability" in diff.changed_fields and session_a is not None and session_b is not None:
        supplementary = []
        if "stability_reasoning_summary" in session_a or "stability_reasoning_summary" in session_b:
            summary_a = session_a.get("stability_reasoning_summary")
            summary_b = session_b.get("stability_reasoning_summary")
            supplementary.append(f"  stability_reasoning_summary: {summary_a!r} -> {summary_b!r}")
        if "transition_events" in session_a or "transition_events" in session_b:
            events_a = session_a.get("transition_events")
            events_b = session_b.get("transition_events")
            supplementary.append(f"  transition_events: {events_a!r} -> {events_b!r}")
        if supplementary:
            lines.append("  [supplementary, non-canonical context -- not part of the ordered divergence chain]")
            lines.extend(supplementary)

    return "\n".join(lines)
