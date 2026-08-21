"""The read end of the learning loop.

D-8 made the OutcomeMemoryRecord durable: every closed position is written to
an EventStore that survives the process. Nothing ever read it back.

Meanwhile `governor_context_builder` asks the AdaptiveRiskMemory real
questions on every entry decision -- `all_entries()` and
`lookup(strategy_type=...)` -- and the runner handed it a memory built by
`AdaptiveRiskMemory()`: fresh and empty, every session, forever. Nothing in
the codebase ever called `append_observation` either.

So the risk chain was asking questions of a memory that could not answer, and
Bujji wrote down every outcome and never opened the book. That is the
write-only-memory anti-pattern the charter names, one level above the one D-8
fixed -- the record survives, the memory that could use it does not.

This module is the bridge: durable outcomes in, risk-memory entries out.

WHAT IT REFUSES TO INVENT. RiskMemoryEntry wants fields an OutcomeMemoryRecord
does not carry. `volatility_regime` is required by the entry and absent from
the record, so it is recorded as UNKNOWN and NAMED in the entry's notes rather
than back-filled from today's regime -- a past trade's volatility is not
today's, and guessing it would poison exactly the lookups the risk chain
performs. `max_drawdown`/`max_profit` come from the record's own mfe/mae,
which read None on every real record today (see outcome_attribution's
docstring); they stay None rather than being derived from realized_pnl, which
would describe the ending rather than the excursion.

DECISION-AFFECTING, AND INERT UNTIL THERE IS A TRADE. Hydrating this memory
means past outcomes begin informing risk context. With zero closed positions
the store is empty and the behaviour is identical to before; it becomes real
with the first trade, which is the point.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from bujji.trading_brain.risk_governor.adaptive_risk_memory import (
    OUTCOME_BREAKEVEN, OUTCOME_LOSS, OUTCOME_UNKNOWN, OUTCOME_WIN,
    AdaptiveRiskMemory, build_risk_memory_entry,
)

# outcome_attribution speaks PROFIT; the risk memory speaks WIN. Everything
# else already agrees. Mapped explicitly so a new value on either side fails
# loudly as UNKNOWN rather than silently becoming a win.
_OUTCOME_MAP = {
    "PROFIT": OUTCOME_WIN,
    "LOSS": OUTCOME_LOSS,
    "BREAKEVEN": OUTCOME_BREAKEVEN,
    "UNKNOWN": OUTCOME_UNKNOWN,
}

_VOLATILITY_ABSENT_NOTE = (
    "volatility_regime unavailable: OutcomeMemoryRecord does not carry it, and "
    "today's regime is not this trade's -- recorded UNKNOWN, never back-filled"
)


@dataclass(frozen=True)
class HydrationReport:
    """What was actually loaded, and what was not."""

    entries_loaded: int = 0
    records_seen: int = 0
    records_skipped: int = 0
    skip_reasons: Tuple[str, ...] = ()
    store_path: Optional[str] = None
    status: str = "OK"
    reason: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "entries_loaded": self.entries_loaded, "records_seen": self.records_seen,
            "records_skipped": self.records_skipped,
            "skip_reasons": list(self.skip_reasons), "store_path": self.store_path,
            "status": self.status, "reason": self.reason,
        }


def risk_entry_from_outcome(record: Any, *, clock):
    """One durable outcome -> one risk-memory entry, or None.

    None whenever the record cannot honestly become an entry -- a missing
    strategy family or memory id makes the entry unattributable, and an
    unattributable entry pollutes every lookup that follows.
    """
    memory_id = getattr(record, "memory_id", None)
    strategy = getattr(record, "strategy_family", None)
    if not memory_id or not strategy:
        return None

    raw_outcome = getattr(record, "outcome_direction", None)
    outcome = _OUTCOME_MAP.get(raw_outcome, OUTCOME_UNKNOWN)

    notes = [_VOLATILITY_ABSENT_NOTE]
    cause = getattr(record, "primary_cause", None)
    if cause:
        notes.append(f"primary_cause={cause}")
    if raw_outcome not in _OUTCOME_MAP:
        notes.append(f"unmapped outcome_direction={raw_outcome!r} -> UNKNOWN")

    return build_risk_memory_entry(
        entry_id=memory_id,
        strategy_type=strategy,
        market_regime=getattr(record, "entry_regime", None) or OUTCOME_UNKNOWN,
        volatility_regime=OUTCOME_UNKNOWN,
        capital_state=None, portfolio_state=None,
        recommended_size=None, actual_size=None,
        realized_outcome=outcome,
        # mfe/mae are the record's OWN excursion fields. They read None on
        # every real record today; deriving them from realized_pnl would
        # describe how the trade ended, not how it behaved.
        max_drawdown=abs(record.mae) if getattr(record, "mae", None) is not None else None,
        max_profit=getattr(record, "mfe", None),
        holding_period_days=None,
        exit_reason=getattr(record, "final_thesis_status", None),
        notes=tuple(notes), clock=clock,
    )


def hydrate_risk_memory(store_path: str, *, clock) -> Tuple[AdaptiveRiskMemory, HydrationReport]:
    """Build an AdaptiveRiskMemory from the durable outcome store.

    NEVER RAISES. A missing or unreadable store yields an EMPTY memory and a
    report saying so -- which is exactly the behaviour Bujji had before this
    module existed, so a broken store degrades to the old world rather than
    stopping a trading session.
    """
    memory = AdaptiveRiskMemory()
    try:
        from bujji.outcome_memory.recovery import hydrate_outcome_memory
        from bujji.state_persistence.store import EventStore

        records, _ = hydrate_outcome_memory(EventStore(store_path))
    except Exception as exc:  # noqa: BLE001 -- an unreadable memory must not end a session
        return memory, HydrationReport(
            store_path=store_path, status=f"UNAVAILABLE:{type(exc).__name__}",
            reason=str(exc))

    skipped: List[str] = []
    loaded = 0
    for memory_id, record in sorted(records.items()):
        entry = risk_entry_from_outcome(record, clock=clock)
        if entry is None:
            skipped.append(f"{memory_id}: not attributable (missing strategy_family/memory_id)")
            continue
        try:
            memory.append_observation(entry)
            loaded += 1
        except Exception as exc:  # noqa: BLE001 -- one bad entry never blocks the rest
            skipped.append(f"{memory_id}: {type(exc).__name__}: {exc}")

    return memory, HydrationReport(
        entries_loaded=loaded, records_seen=len(records), records_skipped=len(skipped),
        skip_reasons=tuple(skipped[:10]), store_path=store_path)
