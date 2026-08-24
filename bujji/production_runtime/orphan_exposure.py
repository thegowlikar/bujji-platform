"""Broker exposure no journal group claims -- recorded, and recoverable.

WHAT AN ORPHAN IS. The broker reports a position and nothing in the journal
says Bujji opened it. Either a record was lost, or something outside Bujji
traded the account. Both are conditions an operator must resolve, and neither
is a reason to leave the position open: flattening it is the correct risk
action, because refusing leaves naked overnight option exposure.

WHY IT NEEDS ITS OWN RECORD. The flatten itself is easy; the recovery is not.
An orphan flatten that is only logged is a side channel -- a process that dies
mid-flatten leaves an order at the venue that no later run can find, and the
next run rediscovers the same position and sends a second one. So the
discovery is journaled BEFORE the exit is planned, its exit attempts are
journaled before they are placed, and a restart reads both back.

WHERE IT LIVES, AND WHY THAT IS NOT A SECOND STORE. Orphan records are
ORPHAN_EXPOSURE_RECORDED events under the session identity, in the SAME
journal as everything else. They are session-scoped because there is no
position group to attach them to and minting one would make Bujji claim it
opened a position it did not -- the synthetic-exposure defect this campaign
removed. `apply_event_to_state` does not recognise the type and
`position_group_ids()` excludes the scope, so margin, reconciliation, group
enumeration and reconstructed exposure never see them. It is a session-scoped
REPRESENTATION of broker exposure, not a position and not a store.

THE ONE WAY OUT. A record reaches RESOLVED_FLAT only on broker CONFIRMED_FLAT
-- the validator refuses to write the terminal state on any other broker
truth. UNKNOWN, a missing read, an inconsistent one, or corrupt evidence all
block entry instead, because a session that cannot establish whether it holds
unaccounted exposure must not take new risk on top of it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from bujji.production_runtime import exit_lifecycle as _exit_lifecycle
from bujji.production_runtime.position_group_scope import (
    append_scoped_event, is_session_scoped, session_scope_ids)
from bujji.trading_brain.risk_governor.position_group_validation import (
    ORPHAN_BROKER_OPEN, ORPHAN_BROKER_UNKNOWN, ORPHAN_DISCOVERED,
    ORPHAN_RESOLVED_FLAT)

EVENT_ORPHAN = "ORPHAN_EXPOSURE_RECORDED"

STATE_CLEAR = "NO_ORPHAN_EXPOSURE"
STATE_RESUMED = "ORPHAN_FLATTEN_RESUMED"
STATE_UNSAFE = "ORPHAN_EXPOSURE_UNRESOLVED"


class OrphanEvidenceCorrupt(Exception):
    """An orphan record cannot be read or does not make sense.

    Deliberately fatal to entry rather than skippable. A record we cannot
    interpret is not an absence of exposure -- it is exposure we have lost the
    ability to describe, which is strictly worse.
    """


def orphan_id(session_id, symbol) -> str:
    """Immutable, and readable in a journal dump.

    Keyed by session and symbol: one session discovering the same symbol twice
    is the SAME orphan being re-observed, not a second one, so re-recording it
    updates the record's state rather than forking it.
    """
    return f"ORPHAN|{session_id}|{symbol}"


@dataclass
class OrphanRecord:
    """One orphan's current standing, folded from its event history."""

    orphan_id: str
    session_id: str
    symbol: str
    signed_quantity: int
    contract_id: str
    discovered_at: str
    evidence_reference: str
    record_state: str
    broker_truth_state: Optional[str] = None
    scope: str = ""
    exit_attempts: Tuple[Dict[str, Any], ...] = ()
    broker_client_order_ids: Tuple[str, ...] = ()

    @property
    def is_resolved(self) -> bool:
        """Terminal ONLY on a broker-confirmed flat. Nothing else counts."""
        return self.record_state == ORPHAN_RESOLVED_FLAT

    def to_dict(self) -> Dict[str, Any]:
        return {"orphan_id": self.orphan_id, "session_id": self.session_id,
                "symbol": self.symbol, "signed_quantity": self.signed_quantity,
                "contract_id": self.contract_id,
                "discovered_at": self.discovered_at,
                "evidence_reference": self.evidence_reference,
                "record_state": self.record_state,
                "broker_truth_state": self.broker_truth_state,
                "scope": self.scope, "resolved": self.is_resolved,
                "exit_attempts": [dict(a) for a in self.exit_attempts],
                "broker_client_order_ids": list(self.broker_client_order_ids)}


@dataclass
class OrphanReport:
    """What a startup established about unaccounted broker exposure."""

    state: str = STATE_CLEAR
    inspected: bool = False
    error: str = ""
    unresolved: Tuple[OrphanRecord, ...] = ()
    resolved: Tuple[OrphanRecord, ...] = ()
    resumable: Tuple[OrphanRecord, ...] = ()
    detail: str = ""

    @property
    def blocks_entry(self) -> bool:
        """ANY unresolved record blocks, whatever the state name says.

        Keyed off the RECORDS, not the state label, and deliberately: a
        confirmed-still-open orphan is "resumable", which sounds like progress
        and is not resolution. Only a broker-confirmed flat resolves a record,
        so anything short of that means the account holds exposure this
        process cannot account for -- and a session must not take new risk on
        top of that.

        UNINSPECTED BLOCKS TOO. "The check did not run" and "the check found
        nothing" must never reach the same decision -- that conflation is how
        a failure to look presents as having looked.
        """
        return (not self.inspected) or bool(self.unresolved)

    def operator_instructions(self) -> str:
        if not self.inspected:
            return (f"orphan-exposure inspection did not complete ({self.error}). "
                    f"Whether the account holds exposure Bujji never opened was "
                    f"NOT established. Entry is refused.")
        if not self.unresolved:
            return ""
        lines = [f"{len(self.unresolved)} unresolved orphan exposure record(s) "
                 f"-- the broker held these and no journal group claims them:"]
        for record in self.unresolved:
            lines.append(
                f"  {record.symbol} x{record.signed_quantity:+d} "
                f"[{record.record_state}, broker={record.broker_truth_state or 'UNREAD'}, "
                f"found {record.discovered_at} by {record.session_id}] "
                f"orders={list(record.broker_client_order_ids) or 'none placed'}")
        lines.append(
            "Reconcile each against the broker's order book. A record clears "
            "ONLY when the broker confirms the account is flat.")
        return "\n".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        return {"state": self.state, "inspected": self.inspected,
                "error": self.error, "detail": self.detail,
                "blocks_entry": self.blocks_entry,
                "unresolved": [r.to_dict() for r in self.unresolved],
                "resolved": [r.to_dict() for r in self.resolved],
                "resumable": [r.to_dict() for r in self.resumable]}


# ---------------------------------------------------------------------------
# WRITE -- the discovery, journaled before any exit is planned.
# ---------------------------------------------------------------------------

def record_discovery(journal, *, session_id, symbol, signed_quantity,
                     contract_id, discovered_at, evidence_reference, clock,
                     logger=None) -> str:
    """Journal an orphan BEFORE its exit is planned, and return its id.

    Ordering matters for the same reason exit intent precedes placement: a
    process that dies after flattening but before recording leaves an order at
    the venue and no trace of why it was sent.
    """
    identifier = orphan_id(session_id, symbol)
    append_scoped_event(
        journal, _exit_lifecycle.session_scope_id(session_id), EVENT_ORPHAN,
        f"{identifier}:{ORPHAN_DISCOVERED}",
        {"orphan_id": identifier, "session_id": session_id, "symbol": symbol,
         "signed_quantity": int(signed_quantity), "contract_id": contract_id,
         "discovered_at": discovered_at,
         "evidence_reference": evidence_reference,
         "record_state": ORPHAN_DISCOVERED,
         "broker_truth_state": "CONFIRMED_OPEN"},
        clock=clock, logger=logger)
    if logger is not None:
        logger.critical(
            "ORPHAN EXPOSURE RECORDED -- %s x%+d, found by %s (%s). The broker "
            "holds this and no journal group claims it. It will be flattened; "
            "its provenance needs operator review.",
            symbol, int(signed_quantity), session_id, evidence_reference)
    return identifier


def record_broker_observation(journal, record, broker_truth_state, clock,
                              logger=None):
    """Update an orphan with what the broker last said.

    RESOLVED_FLAT is not reachable from here by accident: the state is chosen
    from the broker's own answer, and the validator refuses the terminal state
    on anything but CONFIRMED_FLAT.
    """
    if broker_truth_state == "CONFIRMED_FLAT":
        state = ORPHAN_RESOLVED_FLAT
    elif broker_truth_state == "CONFIRMED_OPEN":
        state = ORPHAN_BROKER_OPEN
    else:
        state = ORPHAN_BROKER_UNKNOWN
    return append_scoped_event(
        journal, record.scope or _exit_lifecycle.session_scope_id(record.session_id),
        EVENT_ORPHAN, f"{record.orphan_id}:{state}:{broker_truth_state}",
        {"orphan_id": record.orphan_id, "session_id": record.session_id,
         "symbol": record.symbol, "signed_quantity": int(record.signed_quantity),
         "contract_id": record.contract_id,
         "discovered_at": record.discovered_at,
         "evidence_reference": record.evidence_reference,
         "record_state": state, "broker_truth_state": broker_truth_state},
        clock=clock, logger=logger)


# ---------------------------------------------------------------------------
# READ -- fold the records back, across sessions.
# ---------------------------------------------------------------------------

def _fold_records(journal, scope) -> Dict[str, OrphanRecord]:
    try:
        events = journal.read_events(scope)
    except Exception as exc:  # noqa: BLE001
        raise OrphanEvidenceCorrupt(
            f"cannot read session scope {scope}: {type(exc).__name__}: {exc}"
        ) from exc

    records: Dict[str, OrphanRecord] = {}
    for event in events or ():
        if getattr(event, "event_type", None) != EVENT_ORPHAN:
            continue
        payload = getattr(event, "payload", None) or {}
        identifier = payload.get("orphan_id")
        if not identifier:
            raise OrphanEvidenceCorrupt(
                f"an orphan record in {scope} carries no orphan_id -- exposure "
                f"we have lost the ability to describe is worse than exposure "
                f"described plainly")
        try:
            records[identifier] = OrphanRecord(
                orphan_id=identifier,
                session_id=payload["session_id"], symbol=payload["symbol"],
                signed_quantity=int(payload["signed_quantity"]),
                contract_id=payload["contract_id"],
                discovered_at=payload["discovered_at"],
                evidence_reference=payload["evidence_reference"],
                record_state=payload["record_state"],
                broker_truth_state=payload.get("broker_truth_state"),
                scope=scope)
        except (KeyError, TypeError, ValueError) as exc:
            raise OrphanEvidenceCorrupt(
                f"orphan record {identifier} in {scope} is malformed "
                f"({type(exc).__name__}: {exc})") from exc
    return records


def _attach_attempt_history(journal, record) -> OrphanRecord:
    """Link the orphan to the exit attempts made against it.

    Same journal, same scope, joined on the target contract -- so the record
    carries what was actually sent without a second store holding a copy that
    can disagree.
    """
    history = [h for h in _exit_lifecycle.attempt_history(journal, record.scope)
               if h.get("target_contract_id") == record.contract_id]
    record.exit_attempts = tuple(history)
    record.broker_client_order_ids = tuple(dict.fromkeys(
        h["broker_client_order_id"] for h in history
        if h.get("broker_client_order_id")))
    return record


def inspect(journal, broker_truth, logger=None) -> OrphanReport:
    """What unaccounted exposure does the account carry, right now?

    Called at STARTUP, before entry, across EVERY session scope in the journal
    -- an orphan a previous process discovered and failed to close is exactly
    the one this process must not trade on top of.

    NEVER RAISES. Every failure becomes a report that blocks entry, because an
    exception here would surface as a crash rather than as a safety decision.
    """
    report = OrphanReport()
    try:
        scopes = session_scope_ids(journal)
    except Exception as exc:  # noqa: BLE001
        report.error = f"{type(exc).__name__}: {exc}"
        report.detail = "the journal's session scopes could not be enumerated"
        return report

    all_records: List[OrphanRecord] = []
    try:
        for scope in scopes:
            for record in _fold_records(journal, scope).values():
                all_records.append(_attach_attempt_history(journal, record))
    except OrphanEvidenceCorrupt as exc:
        report.error = str(exc)
        report.detail = (
            "orphan evidence is corrupt; whether the account holds unaccounted "
            "exposure could not be established")
        if logger is not None:
            logger.critical("ORPHAN EVIDENCE CORRUPT -- %s. Entry is refused.", exc)
        return report

    report.inspected = True
    resolved = [r for r in all_records if r.is_resolved]
    outstanding = [r for r in all_records if not r.is_resolved]
    report.resolved = tuple(resolved)

    if not outstanding:
        report.state = STATE_CLEAR
        report.detail = (f"{len(resolved)} orphan record(s), all resolved by a "
                         f"broker-confirmed flat" if resolved
                         else "no orphan exposure recorded")
        return report

    # There ARE unresolved records. Only the broker can say whether they are
    # still open, and only a CONFIRMED answer counts.
    report.unresolved = tuple(outstanding)
    if broker_truth is None or getattr(broker_truth, "is_unknown", True):
        report.state = STATE_UNSAFE
        state_name = getattr(broker_truth, "state", None) if broker_truth else None
        report.detail = (
            f"{len(outstanding)} unresolved orphan record(s) and broker truth is "
            f"{state_name or 'unreadable'} -- whether they are still open could "
            f"not be established. UNKNOWN is not FLAT.")
        if logger is not None:
            logger.critical("ORPHAN EXPOSURE UNRESOLVED -- %s", report.detail)
        return report

    open_symbols = set(getattr(broker_truth, "symbols", ()) or ())
    still_open = [r for r in outstanding if r.symbol in open_symbols]
    gone = [r for r in outstanding if r.symbol not in open_symbols]

    if still_open:
        # RESUMABLE, NOT DUPLICABLE. The caller continues the SAME workflow
        # from the recorded attempt history; exit_lifecycle refuses to re-send
        # an attempt whose fate is unknown and skips one already acknowledged.
        report.state = STATE_RESUMED
        report.resumable = tuple(still_open)
        report.detail = (
            f"{len(still_open)} orphan(s) confirmed STILL OPEN at the broker "
            f"-- resuming the recorded flatten workflow, not starting a new one")
        if logger is not None:
            logger.critical("ORPHAN EXPOSURE STILL OPEN -- %s", report.detail)
        return report

    # The broker answered and does not hold them. That is the ONE thing that
    # resolves a record -- and it must be written, not merely observed, or the
    # next restart rediscovers the same orphan.
    report.state = STATE_UNSAFE
    report.detail = (
        f"{len(gone)} orphan record(s) are absent from a broker book that was "
        f"read successfully, but no RESOLVED_FLAT has been written for them "
        f"yet -- call resolve_absent() to settle them")
    return report


def resolve_absent(journal, report, broker_truth, clock, logger=None) -> List[str]:
    """Write RESOLVED_FLAT for records the broker confirms it no longer holds.

    Refuses on anything but a CONFIRMED_FLAT-or-OPEN answer: a record must not
    be settled against a book we could not read.
    """
    if broker_truth is None or getattr(broker_truth, "is_unknown", True):
        raise OrphanEvidenceCorrupt(
            "orphan records cannot be resolved against a broker book that "
            "could not be read -- UNKNOWN is not FLAT")
    open_symbols = set(getattr(broker_truth, "symbols", ()) or ())
    settled = []
    for record in report.unresolved:
        if record.symbol in open_symbols:
            continue
        record_broker_observation(journal, record, "CONFIRMED_FLAT", clock, logger)
        settled.append(record.orphan_id)
        if logger is not None:
            logger.info(
                "ORPHAN RESOLVED -- %s is absent from a broker book that was "
                "read successfully.", record.symbol)
    return settled
