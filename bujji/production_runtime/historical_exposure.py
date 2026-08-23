"""Exposure the journal still carries from an earlier trading day.

THE CASE THIS EXISTS FOR, and it is a real one sitting in the live journal.

On 2026-08-21 a session entered a two-leg position, traded it, and flattened
the account. The exit was never journaled -- `eod_closure` wrote nothing at
all -- so `PG-96e5dc16a83f204c31bb` folds OPEN forever, with both legs ACKED
at net 65. The account is flat. The record says it is not.

What that produced was NOT an error message. `read_all_group_ids` has no date
scope, so every later session inherits the group; the runner passes empty
contract maps to `project_whole_book_to_margin_legs`; the projection raises
`IllegalMarginProjectionInputError` on legs it cannot map; and that becomes
`ContextUnavailable("margin_snapshot")`. Every entry is refused, and the
reported reason is that margin was unavailable.

THAT IS THE DEFECT THIS MODULE FIXES -- not the refusal.

The refusal is correct: a session that cannot reconcile historical exposure
must not trade. What was wrong is that it arrived as a margin-subsystem crash
naming nothing an operator could act on, and it arrived by accident. A stale
position group is a RECOVERY state: explicit, named, UNKNOWN, unsafe, and
carrying the exact reconciliation an operator has to perform.

WHY IT IS NOT DELETED OR SOFTENED. The group is forensic evidence of a real
defect -- an exit that could not be recorded because nothing wrote it. Clearing
it silently would erase the only durable trace that it happened, and the same
class of bug would be invisible next time.

THE OPERATOR PATH. `OPERATOR_CORRECTION_RECORDED` is the journal's own
audit-only vocabulary for a human resolution: it records the statement and
deliberately mutates no leg, no lifecycle_state and no closure_reason, so the
forensic trail survives intact. This module honours such a correction by
excluding the group from the blocking set -- the runtime's decision, made
outside the frozen fold, on a durable statement a person signed.
"""
from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from bujji.core.clock import IST
from bujji.production_runtime.position_group_scope import position_group_ids
from bujji.trading_brain.risk_governor.position_group_fold import fold, net_quantity

# The lifecycle states that mean "this group still carries exposure", matching
# whole_book_margin_provider._ACTIVE_LIFECYCLE_STATES -- the same set whose
# projection was crashing.
_ACTIVE = ("CONSTRUCTED", "OPEN", "PARTIALLY_OPEN")

CORRECTION_EVENT = "OPERATOR_CORRECTION_RECORDED"

# What an operator writes into a correction's payload to state that a stale
# group has been reconciled by hand.
#
# It rides ALONGSIDE the fields the journal already requires of an
# OPERATOR_CORRECTION_RECORDED -- operator_id, justification,
# evidence_reference, reviewed_at, correcting_event_type, correcting_payload.
# That schema was already the right one: it demands a named person, a stated
# reason, and a reference to what they checked. This adds only the marker that
# says WHICH question they were answering.
RESOLUTION_KEY = "historical_exposure_resolved"

REQUIRED_CORRECTION_FIELDS = (
    "correcting_event_type", "correcting_payload", "operator_id",
    "justification", "evidence_reference", "reviewed_at")


@dataclass
class StaleGroup:
    position_group_id: str
    lifecycle_state: str
    last_event_date: Optional[str]
    open_legs: Tuple[str, ...]
    net_quantity: int

    def to_dict(self) -> Dict[str, Any]:
        return {"position_group_id": self.position_group_id,
                "lifecycle_state": self.lifecycle_state,
                "last_event_date": self.last_event_date,
                "open_legs": list(self.open_legs),
                "net_quantity": self.net_quantity}


@dataclass
class HistoricalExposureReport:
    """What earlier days left in the record, and what to do about it."""

    inspected: bool = False
    stale: List[StaleGroup] = field(default_factory=list)
    resolved: List[str] = field(default_factory=list)
    broker_state: Optional[str] = None
    error: Optional[str] = None

    @property
    def blocks_entry(self) -> bool:
        """Unresolved historical exposure, or an inspection that could not
        run. Both mean the account cannot be reconciled, and a session that
        cannot reconcile may not take new risk."""
        return bool(self.stale) or not self.inspected

    def operator_instructions(self) -> str:
        """Exactly what a person has to do, naming the groups and the legs."""
        if not self.stale:
            return ""
        lines = [
            "HISTORICAL UNRESOLVED EXPOSURE -- this session will not enter.",
            "",
            "The position group journal still records open exposure from an",
            "earlier trading day. The broker reports the account as "
            f"{self.broker_state or 'UNREADABLE'}.",
            "",
        ]
        for g in self.stale:
            lines.append(f"  {g.position_group_id}  [{g.lifecycle_state}]"
                         f"  last event {g.last_event_date}"
                         f"  net {g.net_quantity}")
            for leg in g.open_legs:
                lines.append(f"      leg {leg}")
        lines += [
            "",
            "RECONCILE BEFORE THE NEXT SESSION:",
            "  1. Confirm against the broker's own records whether this",
            "     exposure was actually closed. Do NOT assume it was because",
            "     the account is flat today -- that is what has to be proven.",
            "  2. If it was closed, record a durable operator correction:",
            "",
            "     OPERATOR_CORRECTION_RECORDED on the group, with the fields",
            "     the journal already requires of one --",
            "",
            f'       {RESOLUTION_KEY}: true',
            "       operator_id:          <who reconciled it>",
            "       justification:        <why you concluded it was closed>",
            "       evidence_reference:   <the broker record you checked>",
            "       reviewed_at:          <when>",
            "       correcting_event_type / correcting_payload",
            "",
            "     The correction is AUDIT-ONLY by design: it records the",
            "     statement and changes no leg, no lifecycle state and no",
            "     closure reason, so the forensic trail of the original defect",
            "     survives. This runtime honours it and stops blocking.",
            "  3. If it was NOT closed, the exposure is real and live. Flatten",
            "     it deliberately before any further session.",
        ]
        return "\n".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        return {"inspected": self.inspected,
                "stale": [g.to_dict() for g in self.stale],
                "resolved": list(self.resolved),
                "broker_state": self.broker_state,
                "error": self.error,
                "blocks_entry": self.blocks_entry}


def _event_date(event) -> Optional[str]:
    when = getattr(event, "recorded_at", None)
    if when is None:
        return None
    if getattr(when, "tzinfo", None) is None:
        when = when.replace(tzinfo=IST)
    return when.astimezone(IST).date().isoformat()


def _is_resolved_by_operator(events) -> bool:
    for event in events:
        if getattr(event, "event_type", None) != CORRECTION_EVENT:
            continue
        if (getattr(event, "payload", {}) or {}).get(RESOLUTION_KEY) is True:
            return True
    return False


def inspect(journal, trading_date: str, broker_truth, logger=None
            ) -> HistoricalExposureReport:
    """Find exposure the journal carries from BEFORE `trading_date`.

    Never raises: this runs at startup, and a bookkeeping failure must not stop
    a session from starting -- but `inspected` stays False if it could not run,
    and that blocks entry just as a stale group does. A check that did not run
    is not a clean result.
    """
    report = HistoricalExposureReport()
    report.broker_state = getattr(broker_truth, "state", None) if broker_truth else None
    try:
        for group_id in position_group_ids(journal):
            events = journal.read_events(group_id)
            if not events:
                continue
            state = fold(events)
            if state.lifecycle_state not in _ACTIVE:
                continue
            # DATED BY POSITION ACTIVITY, NOT BY METADATA ABOUT IT.
            #
            # An OPERATOR_CORRECTION_RECORDED is a statement ABOUT the group,
            # written whenever a person got to it -- typically today. Counting
            # it as activity made a correction dated today reclassify a
            # historical group as "today's own work", so it silently dropped
            # out of BOTH the stale and the resolved lists. A test caught it.
            dates = [d for d in (_event_date(e) for e in events
                                 if getattr(e, "event_type", None) != CORRECTION_EVENT)
                     if d]
            last = max(dates) if dates else None
            if last is not None and last >= trading_date:
                continue                      # today's own work, not history
            if _is_resolved_by_operator(events):
                report.resolved.append(group_id)
                continue
            open_legs = tuple(sorted(
                str(leg.contract_id or coid)
                for coid, leg in state.legs.items() if net_quantity(leg) > 0))
            report.stale.append(StaleGroup(
                position_group_id=group_id,
                lifecycle_state=state.lifecycle_state,
                last_event_date=last, open_legs=open_legs,
                net_quantity=sum(net_quantity(l) for l in state.legs.values())))
        report.inspected = True
    except Exception as exc:  # noqa: BLE001
        report.error = f"{type(exc).__name__}: {exc}"
        if logger is not None:
            logger.critical(
                "HISTORICAL EXPOSURE INSPECTION FAILED (%s). Treated as NOT "
                "established, never as nothing found.", report.error)
        return report

    if report.stale and logger is not None:
        logger.critical("%s", report.operator_instructions())
    return report
