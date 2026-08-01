"""Position Group minting — BUJJI Options OS v3, Numeric Risk Governor
Gate A.

Mints a `position_group_id` at the approved trade-intent boundary: the
moment `position_sizing_engine.size_position()` returns a PositionPlan
with `validation == taxonomy.VALIDATION_STATUS_PASSED`. This module
never calls that engine itself -- it is called by the production
runtime immediately after, with the resulting PositionPlan already in
hand. No modification to position_sizing/engine.py.

Collision handling mirrors bujji.runtime_session.engine's own
established precedent (see RuntimeSession.create_session): compute a
candidate ID, attempt to record it, and if a concurrent mint for the
identical plan_id already won the race (enforced by the journal's own
ux_mint_per_plan unique index, not by an application-level check-then-
write), read back and return the winning event rather than proceeding
with two groups for one trade intent.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Optional

from bujji.journal.position_group_journal import MintUniquenessViolation, PositionGroupJournal

Clock = Callable[[], datetime]


def _real_clock() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class MintResult:
    position_group_id: str
    newly_minted: bool  # False if an existing MINTED event for this plan_id was found/won the race


def mint_position_group_id(
    journal: PositionGroupJournal,
    plan_id: str,
    strategy_id: str,
    underlying: str,
    clock: Clock = _real_clock,
) -> MintResult:
    existing = journal.find_mint_by_plan_id(plan_id)
    if existing is not None:
        return MintResult(position_group_id=existing.position_group_id, newly_minted=False)

    timestamp = clock()
    seed = "|".join([plan_id, strategy_id, timestamp.isoformat()])
    candidate_id = "PG-" + hashlib.sha256(seed.encode()).hexdigest()[:20]

    idempotency_key = f"{candidate_id}:MINTED:0"
    payload = {"plan_id": plan_id, "strategy_id": strategy_id, "underlying": underlying}

    try:
        event = journal.append_event(
            position_group_id=candidate_id,
            event_type="MINTED",
            idempotency_key=idempotency_key,
            payload=payload,
            clock=clock,
        )
    except MintUniquenessViolation:
        # A concurrent mint for the SAME plan_id won the race, enforced
        # atomically by the journal's ux_mint_per_plan unique index --
        # not detected by re-reading after the fact. Use the winner.
        winner = journal.find_mint_by_plan_id(plan_id)
        if winner is None:
            raise  # should be unreachable: the constraint only fires if a winner exists
        return MintResult(position_group_id=winner.position_group_id, newly_minted=False)

    if event is None:
        # Our own idempotency_key already existed -- this exact call is
        # itself a retried duplicate; the group already exists under
        # this candidate_id.
        return MintResult(position_group_id=candidate_id, newly_minted=False)

    return MintResult(position_group_id=candidate_id, newly_minted=True)
