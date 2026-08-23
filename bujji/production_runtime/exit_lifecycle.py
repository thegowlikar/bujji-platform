"""Exits enter the journaled state machine BEFORE they reach the broker.

M4b. Exit attempts are ORDERED HISTORY ON THE EXPOSURE GROUP, never groups of
their own.

WHY THAT DISTINCTION IS THE WHOLE MODEL. A position group means underlying
exposure. An exit attempt is not exposure -- it is an event in the life of
exposure that already exists. The first draft of this module recorded attempts
by minting a group per attempt, and that was wrong in a way worth keeping
written down: an acked-but-unfilled exit group folds to CONSTRUCTED, which is
in whole_book_margin_provider._ACTIVE_LIFECYCLE_STATES, so the order placed to
REDUCE exposure was counted as exposure and doubled the measured book. Group
enumeration went from one group to one per attempt with it, and every
consumer that walks groups -- reconciliation, recovery, reconstructed exposure
-- would have walked the synthetic ones too.

SO: ONE EXPOSURE, ONE GROUP, MANY ATTEMPTS.

    exposure group  PG:...              minted once, at entry, by the entry path
      EXIT_ATTEMPT_RECORDED  attempt=1  INTENT -> REJECTED
      EXIT_ATTEMPT_RECORDED  attempt=2  INTENT -> UNKNOWN
      EXIT_ATTEMPT_RECORDED  attempt=3  INTENT -> ACKED -> FILLED
      TARGET_GROUP_REDUCTION_APPLIED    <- the only event that moves exposure

`EXIT_ATTEMPT_RECORDED` is invisible to `apply_event_to_state`, so the group
folds exactly as it would without it: margin, reconciliation, enumeration and
reconstructed exposure are untouched by attempt history, BY CONSTRUCTION.
Exposure moves only through TARGET_GROUP_REDUCTION_APPLIED, unchanged here.

THE RULE THIS ENFORCES, and it is the one the entry path already follows: an
order that the journal does not know about is unrecoverable after a crash. So
intent is journaled first, and only then may a placement be attempted.

    plan()      -> journal one EXIT_ATTEMPT_RECORDED(INTENT) per leg to exit,
                   before any broker call. Returns the plan, or refuses.
    record_*()  -> each attempt's fate, as the broker reports it
    settle()    -> EXITED only when broker truth proves flat AND every attempt
                   has a terminal fate

EXITED IS NOT REACHABLE FROM LOCAL INTENT. Not from an empty cache, not from
an exception, not from "we sent the order and saw nothing since".

ATTEMPT IDENTITY IS IMMUTABLE AND PER-ATTEMPT; GROUP IDENTITY IS STABLE. Each
attempt gets its own `exit_attempt_id` and its own broker client order id, so
a retry can never collide with an order the broker already saw. The exposure
group's identity never changes, so everything downstream keeps seeing the one
position it always saw.

DUPLICATE EXITS ARE PREVENTED BY RECONCILIATION, NOT BY MEMORY. `outstanding()`
reads the journal: a leg whose exit is already acknowledged or filled is not
re-sent, and a leg whose latest attempt has an unknown fate blocks rather than
being re-sent blind. Local state being incomplete is never a reason to place a
second exit.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from bujji.production_runtime.position_group_scope import (
    SESSION_SCOPE_PREFIX, append_scoped_event, session_scope_ids)
from bujji.trading_brain.risk_governor.position_group_fold import (
    fold, net_quantity)
from bujji.trading_brain.risk_governor.position_group_validation import (
    EXIT_ATTEMPT_ACKED, EXIT_ATTEMPT_CANCELLED, EXIT_ATTEMPT_FILLED,
    EXIT_ATTEMPT_INTENT, EXIT_ATTEMPT_RECONCILED, EXIT_ATTEMPT_REJECTED,
    EXIT_ATTEMPT_TERMINAL_STATES, EXIT_ATTEMPT_UNKNOWN)

def session_scope_id(session_id) -> str:
    """Where an ORPHAN exit's history goes. Not a position group.

    `position_group_ids()` filters this namespace out, so nothing that
    measures exposure can see it -- which is exactly why it is safe to record
    an exit against exposure Bujji never recorded opening.
    """
    return f"{SESSION_SCOPE_PREFIX}{session_id}"


def is_orphan_scope(exposure_position_group_id) -> bool:
    return str(exposure_position_group_id).startswith(SESSION_SCOPE_PREFIX)


EVENT_EXIT_ATTEMPT = "EXIT_ATTEMPT_RECORDED"
EVENT_REDUCTION = "TARGET_GROUP_REDUCTION_APPLIED"

# Why an exit is being attempted. Recorded, never inferred.
CAUSE_EOD = "eod_close"
CAUSE_EMERGENCY = "emergency_close"
CAUSE_STRATEGY = "strategy_exit"
CAUSE_ABORT = "abort_flatten"
ALL_CAUSES = (CAUSE_EOD, CAUSE_EMERGENCY, CAUSE_STRATEGY, CAUSE_ABORT)

# An attempt is SETTLED when the exposure is gone or the order is live.
_SETTLED_STATES = (EXIT_ATTEMPT_ACKED, EXIT_ATTEMPT_FILLED)

OUTCOME_FLAT = "CONFIRMED_FLAT"
OUTCOME_OPEN = "STILL_OPEN"
OUTCOME_UNKNOWN = "UNKNOWN"


class ExitPlanRefused(Exception):
    """The exit could not be journaled, so nothing was placed.

    Deliberately an exception rather than an empty plan: a caller that treats
    "no legs to exit" and "I could not record the intent" the same way will
    eventually place an unjournaled order.
    """


class ExitJournalUnreadable(Exception):
    """The journal could not be read, so nothing may be placed.

    NOT the same as "nothing outstanding". An unreadable journal that returned
    an empty result would let a retry re-send every leg blind, which is the
    exact duplicate this module exists to prevent.
    """


@dataclass(frozen=True)
class ExitLeg:
    """One attempt to close one contract of one exposure group."""

    exit_attempt_id: str
    broker_client_order_id: str
    exposure_position_group_id: str
    target_contract_id: str
    quantity: int


@dataclass
class ExitPlan:
    cause: str
    legs: Tuple[ExitLeg, ...] = ()
    journaled: bool = False
    refused: Tuple[Dict[str, Any], ...] = ()
    """Holdings this plan will NOT place, and why.

    Almost always an attempt whose fate nobody knows. They are handed back
    rather than silently dropped, and rather than blocking the siblings:
    refusing to flatten a leg we CAN flatten because a DIFFERENT leg is
    unreadable leaves more exposure open, not less.
    """

    def to_dict(self) -> Dict[str, Any]:
        return {"cause": self.cause, "journaled": self.journaled,
                "legs": [l.broker_client_order_id for l in self.legs],
                "attempt_ids": [l.exit_attempt_id for l in self.legs],
                "refused": [dict(r) for r in self.refused]}


@dataclass
class ExitOutcome:
    """What the closure actually established."""

    outcome: str = OUTCOME_UNKNOWN
    detail: str = ""
    unresolved_legs: List[Dict[str, Any]] = field(default_factory=list)
    broker_state: Optional[str] = None
    open_symbols: Tuple[str, ...] = ()

    @property
    def proves_flat(self) -> bool:
        """The ONLY thing a caller may turn into EXITED."""
        return self.outcome == OUTCOME_FLAT

    def to_dict(self) -> Dict[str, Any]:
        return {"outcome": self.outcome, "detail": self.detail,
                "unresolved_legs": list(self.unresolved_legs),
                "broker_state": self.broker_state,
                "open_symbols": list(self.open_symbols)}


# ---------------------------------------------------------------------------
# Reading attempt history off the exposure group.
# ---------------------------------------------------------------------------

def _attempt_events(journal, exposure_group_id) -> List[Any]:
    try:
        events = journal.read_events(exposure_group_id)
    except Exception as exc:  # noqa: BLE001
        raise ExitJournalUnreadable(
            f"cannot read {exposure_group_id}: {type(exc).__name__}: {exc} -- "
            f"refusing to plan, because an empty read is indistinguishable "
            f"from a journal that records every leg as already sent") from exc
    return [e for e in (events or [])
            if getattr(e, "event_type", None) == EVENT_EXIT_ATTEMPT]


def attempt_history(journal, exposure_group_id) -> List[Dict[str, Any]]:
    """Every exit attempt against this exposure, in the order recorded.

    ORDERED HISTORY, NOT A LATEST-WINS SUMMARY. Several rejected, cancelled,
    timed-out or UNKNOWN attempts coexist here; that sequence is the audit
    trail of what was sent and what came back, and collapsing it would lose
    exactly the evidence a recovery needs.
    """
    return [dict(getattr(e, "payload", None) or {})
            for e in _attempt_events(journal, exposure_group_id)]


def _latest_state_per_attempt(journal, exposure_group_id) -> Dict[str, Dict[str, Any]]:
    """The current standing of each attempt, keyed by exit_attempt_id."""
    latest: Dict[str, Dict[str, Any]] = {}
    for payload in attempt_history(journal, exposure_group_id):
        attempt_id = payload.get("exit_attempt_id")
        if attempt_id:
            latest[attempt_id] = payload
    return latest


def outstanding(journal, exposure_group_id) -> Tuple[Dict[str, str], Dict[str, str]]:
    """(settled, unresolved) for this exposure, keyed by TARGET CONTRACT.

    Keyed by the contract being closed, because that is the question a retry
    actually asks -- "is this leg already being exited?" -- and one leg may
    have several attempts behind it.

      settled[contract]    -> the attempt id that acknowledged or filled
      unresolved[contract] -> the attempt id whose fate nobody knows

    RAISES ExitJournalUnreadable rather than reporting nothing outstanding.
    """
    settled: Dict[str, str] = {}
    unresolved: Dict[str, str] = {}
    for attempt_id, payload in _latest_state_per_attempt(
            journal, exposure_group_id).items():
        contract = payload.get("target_contract_id")
        state = payload.get("attempt_state")
        if not contract:
            raise ExitJournalUnreadable(
                f"exit attempt {attempt_id} on {exposure_group_id} names no "
                f"target contract; refusing to plan an exit that might "
                f"duplicate it")
        if state in _SETTLED_STATES:
            settled[contract] = attempt_id
            unresolved.pop(contract, None)
        elif state in EXIT_ATTEMPT_TERMINAL_STATES:
            unresolved.pop(contract, None)  # finished with; a retry may follow
        elif contract not in settled:
            # INTENT and UNKNOWN both land here, and that is correct: an
            # attempt we recorded but never heard back about is an order that
            # may be live at the venue.
            unresolved[contract] = attempt_id
    return settled, unresolved


def _next_attempt_number(journal, exposure_group_id, target_contract_id) -> int:
    seen = {p.get("exit_attempt_id") for p in attempt_history(journal, exposure_group_id)
            if p.get("target_contract_id") == target_contract_id}
    return len(seen) + 1


def make_attempt_id(exposure_group_id, target_contract_id, attempt_number) -> str:
    """Immutable, unique, and readable in a journal dump.

    Carries the exposure group so an attempt id can never be read as belonging
    to a different position, and the attempt number so ordering survives a
    text dump of the journal.
    """
    return f"{exposure_group_id}|EXIT|{target_contract_id}|{attempt_number}"


# ---------------------------------------------------------------------------
# Mapping what the BROKER holds onto the exposure the JOURNAL knows about.
# ---------------------------------------------------------------------------

def exposure_by_contract(journal, position_group_ids_fn=None) -> Dict[str, Dict[str, Any]]:
    """{contract_id: {group, client_order_id, net_quantity}} for held legs.

    WHY THIS EXISTS. An exit attempt is history on an exposure group, so a
    caller flattening what the BROKER reports needs to know which group each
    symbol belongs to. Read from the journal, which is the only thing that
    knows -- the broker reports symbols and quantities, not Bujji's grouping.
    """
    from bujji.production_runtime.position_group_scope import position_group_ids

    enumerate_groups = position_group_ids_fn or position_group_ids
    try:
        group_ids = enumerate_groups(journal)
    except Exception as exc:  # noqa: BLE001
        raise ExitJournalUnreadable(
            f"cannot enumerate exposure groups: {type(exc).__name__}: {exc}") from exc

    held: Dict[str, Dict[str, Any]] = {}
    for group_id in group_ids:
        try:
            events = journal.read_events(group_id)
        except Exception as exc:  # noqa: BLE001
            raise ExitJournalUnreadable(
                f"cannot read {group_id}: {type(exc).__name__}: {exc}") from exc
        if not events:
            continue
        for coid, leg in fold(events).legs.items():
            remaining = net_quantity(leg)
            if remaining <= 0 or not leg.contract_id:
                continue
            held[leg.contract_id] = {
                "position_group_id": group_id, "client_order_id": coid,
                "net_quantity": remaining}
    return held


def holdings_for_symbols(journal, symbols_and_quantities, session_id,
                         signed_quantities=None, evidence_reference=None) -> Tuple[
        List[Tuple[str, str, int]], List[Dict[str, Any]]]:
    """(holdings, orphans) for what the broker reports as open.

    `symbols_and_quantities`: [(symbol, quantity)] straight from broker truth.

    AN ORPHAN IS A BROKER POSITION NO JOURNAL GROUP CLAIMS -- the account holds
    exposure Bujji has no record of opening. It is STILL FLATTENED, and its
    attempt history is recorded against the SESSION identity rather than a
    position group.

    WHY NOT REFUSE IT. Refusing leaves naked overnight option exposure, which
    is a far worse outcome than an exit whose provenance an operator has to
    explain. WHY NOT MINT A GROUP FOR IT: that is the synthetic-exposure
    defect this model exists to remove, and it would make Bujji claim it
    opened a position it did not. The session identity is neither -- it is
    durable, and `position_group_ids()` excludes it by construction.

    Orphans are returned separately as well, so the caller escalates them.
    """
    held = exposure_by_contract(journal)
    session_scope = session_scope_id(session_id)
    holdings, orphans = [], []
    for symbol, quantity in symbols_and_quantities:
        match = held.get(symbol)
        if match is None:
            holdings.append((session_scope, symbol, int(quantity)))
            orphans.append({
                "symbol": symbol, "quantity": int(quantity),
                # SIGNED, because the sign is what says whether flattening
                # this means buying or selling. An unsigned quantity read back
                # after a restart cannot be acted on without guessing the side,
                # and guessing wrong DOUBLES the exposure instead of closing it.
                "signed_quantity": int(
                    (signed_quantities or {}).get(symbol, quantity)),
                "exposure_position_group_id": session_scope,
                "evidence_reference": evidence_reference or (
                    f"broker position read, session {session_id}"),
                "reason": "the broker holds this position and no journal group "
                          "claims it -- Bujji has no record of opening it. It "
                          "is being flattened, but its provenance is unknown"})
            continue
        holdings.append((match["position_group_id"], symbol,
                         min(int(quantity), int(match["net_quantity"]))))
    return holdings, orphans


# ---------------------------------------------------------------------------
# PLAN -- journal the intent, before any broker call.
# ---------------------------------------------------------------------------

def _append_attempt(journal, exposure_group_id, *, attempt_id, broker_coid,
                    cause, attempt_state, target_contract_id, clock,
                    extra=None):
    """The one sanctioned way to write attempt history.

    The idempotency key carries the attempt id AND the state, so each attempt
    has its own row per transition and a replay of the same transition is a
    no-op rather than a duplicate.
    """
    payload = {
        "exit_attempt_id": attempt_id,
        "exposure_position_group_id": exposure_group_id,
        "broker_client_order_id": broker_coid,
        "cause": cause,
        "attempt_state": attempt_state,
        "target_contract_id": target_contract_id,
    }
    payload.update(extra or {})
    # Through the ONE sanctioned append, so the writer-side namespace
    # invariant sees every attempt -- on an exposure group and on a session
    # scope alike. Writing via journal.append_event directly here would make
    # orphan attempts a side channel the invariant never checks, which is how
    # a misdirected event becomes exposure no boundary can see.
    return append_scoped_event(
        journal, exposure_group_id, EVENT_EXIT_ATTEMPT,
        f"{exposure_group_id}:{EVENT_EXIT_ATTEMPT}:{attempt_id}:{attempt_state}",
        payload, clock=clock)


def plan(journal, *, session_id, cause, holdings, broker_truth, clock,
         logger=None) -> ExitPlan:
    """Journal an exit's intent and return what may be placed.

    `holdings`: [(exposure_position_group_id, target_contract_id, quantity)].

    NOTHING IS PLACED BY THIS FUNCTION. It records intent and hands back a
    plan; placement is the caller's, and the caller may only place legs this
    plan contains.

    NOTHING IS MINTED BY THIS FUNCTION EITHER. Every event it writes goes onto
    an exposure group that already exists, so a plan -- or ten retries of one
    -- cannot change what any group query returns.

    RAISES ExitPlanRefused if the intent cannot be journaled: an order the
    journal does not know about is unrecoverable after a crash, so failing to
    record it must stop the placement rather than proceed without it.

    RETRY SAFETY, three rules:
      1. a leg already acknowledged or filled is not re-sent
      2. a leg whose latest attempt has an UNKNOWN or unanswered fate is not
         re-sent either -- it goes to `plan.refused`, because re-sending an
         order whose outcome nobody knows is how one position becomes two
      3. a leg whose attempts have all reached a TERMINAL fate may be retried,
         under a NEW attempt id and a NEW broker order id, so a retry can
         never collide with the order the broker already rejected
    """
    if cause not in ALL_CAUSES:
        raise ExitPlanRefused(
            f"unrecognised exit cause {cause!r}; one of {ALL_CAUSES} is "
            f"required so the journal records WHY a position was closed")

    pending, refused = [], []
    for exposure_group_id, target_contract_id, quantity in holdings:
        try:
            settled, unresolved = outstanding(journal, exposure_group_id)
        except ExitJournalUnreadable as exc:
            raise ExitPlanRefused(str(exc)) from exc
        if target_contract_id in settled:
            continue
        if target_contract_id in unresolved:
            refused.append({
                "exposure_position_group_id": exposure_group_id,
                "target_contract_id": target_contract_id,
                "prior_exit_attempt_id": unresolved[target_contract_id],
                "reason": "a prior exit attempt has an unresolved fate; "
                          "reconcile broker order truth before retrying"})
            continue
        number = _next_attempt_number(journal, exposure_group_id, target_contract_id)
        attempt_id = make_attempt_id(exposure_group_id, target_contract_id, number)
        pending.append(ExitLeg(
            exit_attempt_id=attempt_id,
            # A NEW broker id per attempt. Re-using one the broker already saw
            # risks the venue rejecting it as a duplicate, or -- worse --
            # accepting it as the same order and telling us nothing new.
            broker_client_order_id=f"{session_id}-{cause}-{number}-{target_contract_id}",
            exposure_position_group_id=exposure_group_id,
            target_contract_id=target_contract_id,
            quantity=int(quantity)))

    if not pending:
        return ExitPlan(cause=cause, legs=(), journaled=True, refused=tuple(refused))

    try:
        for leg in pending:
            _append_attempt(
                journal, leg.exposure_position_group_id,
                attempt_id=leg.exit_attempt_id,
                broker_coid=leg.broker_client_order_id, cause=cause,
                attempt_state=EXIT_ATTEMPT_INTENT,
                target_contract_id=leg.target_contract_id, clock=clock,
                extra={"requested_quantity": leg.quantity})
    except Exception as exc:  # noqa: BLE001 -- refusing is the safe outcome
        if logger is not None:
            logger.critical(
                "EXIT REFUSED -- the intent could not be journaled (%s: %s). "
                "Nothing was placed: an order the journal does not know about "
                "is unrecoverable after a crash.", type(exc).__name__, exc)
        raise ExitPlanRefused(
            f"could not journal exit intent: {type(exc).__name__}: {exc}") from exc

    if logger is not None:
        logger.info("EXIT INTENT journaled -- %d leg(s), cause=%s, %d refused",
                    len(pending), cause, len(refused))
    return ExitPlan(cause=cause, legs=tuple(pending), journaled=True,
                    refused=tuple(refused))


# ---------------------------------------------------------------------------
# RECORD -- each attempt's fate, as the broker reports it.
# ---------------------------------------------------------------------------

def record_ack(journal, plan, leg, broker_order_id, broker_status, clock,
               logger=None):
    """The broker acknowledged this exit. It is now LIVE at the venue."""
    return _append_attempt(
        journal, leg.exposure_position_group_id, attempt_id=leg.exit_attempt_id,
        broker_coid=leg.broker_client_order_id, cause=plan.cause,
        attempt_state=EXIT_ATTEMPT_ACKED,
        target_contract_id=leg.target_contract_id, clock=clock,
        extra={"broker_order_id": broker_order_id or leg.broker_client_order_id,
               "broker_reported_status": broker_status})


def record_rejection(journal, plan, leg, reason, clock, logger=None):
    """TERMINAL: the venue said no. This attempt may be retried."""
    return _append_attempt(
        journal, leg.exposure_position_group_id, attempt_id=leg.exit_attempt_id,
        broker_coid=leg.broker_client_order_id, cause=plan.cause,
        attempt_state=EXIT_ATTEMPT_REJECTED,
        target_contract_id=leg.target_contract_id, clock=clock,
        extra={"failure_reason": reason})


def record_cancellation(journal, plan, leg, reason, clock, logger=None):
    """TERMINAL, and only when the venue CONFIRMED the cancel.

    A cancel that was merely requested is not a cancelled order -- the exit
    may still fill -- so the caller must not reach this until the broker says
    the order is gone.
    """
    return _append_attempt(
        journal, leg.exposure_position_group_id, attempt_id=leg.exit_attempt_id,
        broker_coid=leg.broker_client_order_id, cause=plan.cause,
        attempt_state=EXIT_ATTEMPT_CANCELLED,
        target_contract_id=leg.target_contract_id, clock=clock,
        extra={"cancellation_reason": reason})


def record_unknown(journal, plan, leg, detail, clock, logger=None):
    """NOT TERMINAL, on purpose. Nobody knows what happened to this order.

    Recording it is what stops the next attempt re-sending blind. There is no
    path from here to a retry except a reconciliation that establishes what
    the order actually did.
    """
    return _append_attempt(
        journal, leg.exposure_position_group_id, attempt_id=leg.exit_attempt_id,
        broker_coid=leg.broker_client_order_id, cause=plan.cause,
        attempt_state=EXIT_ATTEMPT_UNKNOWN,
        target_contract_id=leg.target_contract_id, clock=clock,
        extra={"unknown_detail": detail})


def record_reconciled(journal, plan, leg, resolution, evidence, clock,
                      logger=None):
    """TERMINAL: broker order truth established that this attempt is gone.

    The ONLY way out of UNKNOWN. `evidence` is required and recorded, because
    "we decided it was fine" and "the order book says it is absent" must not
    look the same in the journal a year later.
    """
    if not evidence:
        raise ExitPlanRefused(
            "reconciling an exit attempt requires evidence -- an assertion "
            "that an order is gone, with nothing behind it, is how an "
            "unresolved order becomes a duplicate")
    return _append_attempt(
        journal, leg.exposure_position_group_id, attempt_id=leg.exit_attempt_id,
        broker_coid=leg.broker_client_order_id, cause=plan.cause,
        attempt_state=EXIT_ATTEMPT_RECONCILED,
        target_contract_id=leg.target_contract_id, clock=clock,
        extra={"resolution": resolution, "evidence_reference": evidence})


def record_fill(journal, plan, leg, filled_quantity, average_price, clock,
                logger=None):
    """The exit filled: record the attempt, THEN move the exposure.

    TWO EVENTS, IN THIS ORDER, AND THE ORDER MATTERS. The attempt record goes
    first because applying the reduction can close the group, and this module
    will not depend on being able to write history to a group after closing
    it. A crash between the two leaves the journal saying "this exit filled"
    with the exposure not yet reduced -- which OVERSTATES what is held, and
    overstating exposure is the safe direction to fail in.

    The reduction is a plain TARGET_GROUP_REDUCTION_APPLIED on the exposure
    group itself. That is the one event that moves a leg, and it is unchanged
    by this module -- exit attempts move nothing.
    """
    _append_attempt(
        journal, leg.exposure_position_group_id, attempt_id=leg.exit_attempt_id,
        broker_coid=leg.broker_client_order_id, cause=plan.cause,
        attempt_state=EXIT_ATTEMPT_FILLED,
        target_contract_id=leg.target_contract_id, clock=clock,
        extra={"filled_quantity": int(filled_quantity),
               "average_fill_price": average_price})

    if int(filled_quantity) <= 0:
        return None
    if is_orphan_scope(leg.exposure_position_group_id):
        # Nothing to reduce: no group ever recorded holding this. The fill is
        # in the attempt history above; inventing a reduction against a leg
        # that does not exist would be a fabricated accounting entry.
        return None
    return append_scoped_event(
        journal, leg.exposure_position_group_id, EVENT_REDUCTION,
        f"{leg.exposure_position_group_id}:{EVENT_REDUCTION}:{leg.exit_attempt_id}",
        {"source_client_order_id": leg.broker_client_order_id,
         "source_position_group_id": leg.exposure_position_group_id,
         "target_contract_id": leg.target_contract_id,
         "reduced_quantity_delta": int(filled_quantity)}, clock=clock)


# ---------------------------------------------------------------------------
# SETTLE -- the only path to EXITED.
# ---------------------------------------------------------------------------

def settle(journal, exposure_group_ids, broker_truth) -> ExitOutcome:
    """Can this closure claim EXITED?

    THREE CONDITIONS, ALL REQUIRED:
      1. broker truth is CONFIRMED_FLAT -- the account itself says so
      2. every leg folds to net zero in the journal
      3. every exit attempt has a terminal fate

    A failure of any one leaves OPEN or UNKNOWN. There is no path from local
    intent, an empty cache, or a swallowed exception to a flat claim.
    """
    outcome = ExitOutcome()
    outcome.broker_state = getattr(broker_truth, "state", None) if broker_truth else None

    if broker_truth is None or getattr(broker_truth, "is_unknown", True):
        outcome.outcome = OUTCOME_UNKNOWN
        outcome.detail = (
            f"broker truth is {outcome.broker_state or 'unreadable'} -- a "
            f"closure cannot be confirmed against an account that cannot be "
            f"read. UNKNOWN is not FLAT.")
        return outcome

    if getattr(broker_truth, "is_open", False):
        outcome.outcome = OUTCOME_OPEN
        outcome.open_symbols = tuple(getattr(broker_truth, "symbols", ()) or ())
        outcome.detail = (
            f"the broker still holds {list(outcome.open_symbols)} -- the "
            f"position is not closed, whatever was submitted")
        return outcome

    try:
        for group_id in exposure_group_ids:
            events = journal.read_events(group_id)
            if not events:
                continue
            state = fold(events)
            for coid, leg in state.legs.items():
                if net_quantity(leg) > 0:
                    outcome.unresolved_legs.append(
                        {"position_group_id": group_id, "client_order_id": coid,
                         "reason": "journal still records this leg as held"})
            for attempt_id, payload in _latest_state_per_attempt(journal, group_id).items():
                attempt_state = payload.get("attempt_state")
                if attempt_state in EXIT_ATTEMPT_TERMINAL_STATES:
                    continue
                if attempt_state == EXIT_ATTEMPT_FILLED:
                    continue
                outcome.unresolved_legs.append(
                    {"position_group_id": group_id, "exit_attempt_id": attempt_id,
                     "reason": f"exit attempt is {attempt_state}, not terminal"})
    except ExitJournalUnreadable as exc:
        outcome.outcome = OUTCOME_UNKNOWN
        outcome.detail = f"the journal could not be read ({exc})"
        return outcome
    except Exception as exc:  # noqa: BLE001
        outcome.outcome = OUTCOME_UNKNOWN
        outcome.detail = f"the journal could not be read ({type(exc).__name__}: {exc})"
        return outcome

    if outcome.unresolved_legs:
        outcome.outcome = OUTCOME_UNKNOWN
        outcome.detail = (
            f"the broker reports flat, but {len(outcome.unresolved_legs)} "
            f"item(s) are not settled in the journal -- agreement is required, "
            f"not a majority")
        return outcome

    outcome.outcome = OUTCOME_FLAT
    outcome.detail = "broker confirms flat and every leg is settled in the journal"
    return outcome
