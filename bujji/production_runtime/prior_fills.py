"""Did this account already deploy its one strategy today?

THE GAP THIS CLOSES. `entry_control.can_enter_trade` refuses a second entry on
STRATEGY_ALREADY_DEPLOYED -- a state in an IN-MEMORY tracker. A restarted
process starts at INITIALIZING with that fact gone.

Reconciliation covers the case where the position is STILL OPEN: after a
restart the registry is empty, so the legs read as BROKER_ONLY, which is
CRITICAL, which blocks new risk. It cannot cover the case where the position
was already EXITED. The account is genuinely flat, the broker cannot
distinguish "never traded today" from "traded and closed today", and every
remaining gate permits entry. The day's second trade follows.

The evidence was already durable: the position group journal records
FILL_OBSERVED with a timestamp, idempotently, and survives the process.
Nothing read it. The deprecated ORB-VWAP bot this system replaced DID handle
this -- `core/orchestrator.py`'s "Clean slate: only carry forward the trade
count (never re-open)" restores DONE_FOR_DAY from a persisted trades_taken.
That property was not carried forward.

WHY THIS LIVES HERE AND NOT ON THE JOURNAL. `bujji/journal/` is frozen --
`tests/test_replay_engine_safety.py` asserts the execution and capital
packages are byte-untouched since 360c003, and a convenience query is not a
reason to break a freeze that exists to protect order handling. This module
uses only the journal's EXISTING public read API, so it adds a caller, not a
second authority: it never touches the table, the schema, or the event
vocabulary.

WHY FILL_OBSERVED AND NOT MINTED OR SUBMIT_INTENT. It mirrors the in-session
rule exactly. A group that minted or submitted but never filled leaves the
tracker in STRATEGY_LOCKED, where a retry is legitimate; only a fill moves it
to POSITION_ACTIVE and burns the day. A submit whose outcome is UNKNOWN is not
this module's problem and must not be: `recover_unresolved_at_startup` resolves
those against broker truth first and refuses to trade at all if any remain
unresolved.
"""
from __future__ import annotations

from typing import List, Tuple

from bujji.core.clock import IST

FILL_EVENT = "FILL_OBSERVED"
UNREADABLE_JOURNAL = "UNREADABLE_JOURNAL"


def group_ids_filled_on(journal, trading_date: str) -> List[str]:
    """Position groups with at least one fill on `trading_date` (ISO
    yyyy-mm-dd, interpreted in IST).

    DATES ARE COMPARED IN IST, NOT BY TEXT. `recorded_at` carries whatever
    timezone its writer's clock used -- the journal defaults to UTC while the
    runner injects IST -- so comparing the stored text would silently mean
    different things for different rows. 20:00 UTC is 01:30 IST the next day.
    """
    found = []
    for group_id in journal.read_all_group_ids():
        for event in journal.read_events(group_id):
            if event.event_type != FILL_EVENT:
                continue
            when = event.recorded_at
            if when is None:
                # A fill with no timestamp is not evidence that nothing was
                # traded. Count it: the safe error is refusing one entry too
                # many, never permitting one too many.
                found.append(group_id)
                break
            if when.tzinfo is None:
                when = when.replace(tzinfo=IST)
            if when.astimezone(IST).date().isoformat() == trading_date:
                found.append(group_id)
                break
    return sorted(set(found))


def prior_fills_snapshot(journal, trading_date: str, logger) -> Tuple[List[str], bool]:
    """(group_ids_filled_today, unreadable). Never raises.

    Separate from the query so the FAIL-CLOSED branch can be tested without
    standing up a session. That branch is the one that matters: a journal we
    could not read is not evidence that nothing was traded, and it must refuse
    exactly as an unreadable position book does.
    """
    try:
        return group_ids_filled_on(journal, trading_date), False
    except Exception as exc:  # noqa: BLE001 -- a failed read is an answer, not a crash
        logger.critical(
            "STARTUP -- could not read prior fills for %s from the position group "
            "journal (%s: %s). Treating the day as ALREADY DEPLOYED and refusing "
            "new entry: an unreadable journal is not an empty one.",
            trading_date, type(exc).__name__, exc)
        return [UNREADABLE_JOURNAL], True
