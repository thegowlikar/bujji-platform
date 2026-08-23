"""The single owner of session and position lifecycle.

M4. Bujji ran TWO session-scoped state machines. `RuntimeState`
(production_runtime.runtime_state_machine) and `TradingSessionState`
(trading_session_governor.session_trading_state) BOTH transitioned to
POSITION_ACTIVE, from different call sites, and BOTH gated entry -- RuntimeState
through `_ENTRY_ACCEPTING_STATES`, TradingSessionState through
`entry_control.can_enter_trade`. Neither was journaled, so neither survived a
restart.

TradingSessionState is the owner. RuntimeState is retired.

WHY THIS ONE. Its states map onto facts the durable journal already holds --
MINTED/CONSTRUCTED means a strategy was locked, FILL_OBSERVED means a position
is active, net-zero means it exited. RuntimeState mixes connectivity
(CONNECTING) and market phase (PREMARKET) with position lifecycle, and those
are PROCESS facts that must NOT survive a restart: after a crash you genuinely
are connecting again, and journaling that would mean reconstructing something
that has to be re-derived fresh. Connectivity and market phase remain derived
process state; they are not restartable lifecycle authority.

TRANSITIONS ARE JOURNALED, STATE IS DERIVED.

Every transition is appended to the SAME durable journal as position events, as
a SESSION_TRANSITION carrying session identity, prior state, next state, cause,
timestamp and an evidence reference. On restart the state is REBUILT from that
event stream, and then RECONCILED against position events and broker truth.

The runner's in-memory tracker becomes a cache. It may not decide whether Bujji
is flat, open, safe to enter, or finished.

DISAGREEMENT IS UNKNOWN, AND UNKNOWN BLOCKS.

The journal says what this process recorded. Broker truth says what the account
holds. When they disagree -- journal says EXITED and the broker holds legs;
journal says POSITION_ACTIVE and the broker is flat -- neither is assumed
correct. The result is UNKNOWN, entry is blocked, and the session is unsafe.
A missing history, a corrupt journal, or a broker UNKNOWN reach the same place
for the same reason: a session that cannot establish what it is may not take
new risk.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from bujji.production_runtime.position_group_scope import (
    SESSION_EVENT_TYPE, append_scoped_event, position_group_ids,
    session_events, session_scope_id)
from bujji.production_runtime.trading_session_governor.session_trading_state import (
    TradingSessionState)
from bujji.trading_brain.risk_governor.position_group_fold import fold, net_quantity

# Not a TradingSessionState member: it is the ABSENCE of an establishable one.
# Deliberately outside the enum so no transition table can accept it as a
# target and no caller can transition INTO it by mistake.
UNKNOWN = "UNKNOWN"

_ALREADY_DEPLOYED = (TradingSessionState.POSITION_ACTIVE,
                     TradingSessionState.MANAGING,
                     TradingSessionState.EXITED)

# States in which the journal asserts a live position exists.
_JOURNAL_SAYS_OPEN = (TradingSessionState.POSITION_ACTIVE,
                      TradingSessionState.MANAGING)


@dataclass(frozen=True)
class SessionLifecycleState:
    """What this session is, and on what evidence.

    `state` is a TradingSessionState, or UNKNOWN. There is no third option and
    no default: a caller that cannot get an answer gets UNKNOWN, which blocks.
    """

    state: Any
    reason: str
    session_id: Optional[str] = None
    journal_state: Optional[Any] = None
    broker_state: Optional[str] = None
    open_symbols: Tuple[str, ...] = ()
    transitions: Tuple[Dict[str, Any], ...] = ()
    evidence: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_unknown(self) -> bool:
        return self.state == UNKNOWN

    @property
    def permits_entry(self) -> bool:
        """The ONLY state a new entry may be taken from.

        Byte-identical to `entry_control.can_enter_trade`'s single allowed
        window: STRATEGY_LOCKED. UNKNOWN joins the refusing set rather than
        widening it -- this is a substitution of input, not a loosening.
        """
        return self.state == TradingSessionState.STRATEGY_LOCKED

    @property
    def already_deployed(self) -> bool:
        return self.state in _ALREADY_DEPLOYED

    def to_dict(self) -> Dict[str, Any]:
        return {
            "state": getattr(self.state, "value", self.state),
            "reason": self.reason,
            "session_id": self.session_id,
            "journal_state": getattr(self.journal_state, "value", self.journal_state),
            "broker_state": self.broker_state,
            "open_symbols": list(self.open_symbols),
            "transition_count": len(self.transitions),
            "evidence": dict(self.evidence),
        }


def _unknown(reason, **kw) -> SessionLifecycleState:
    return SessionLifecycleState(state=UNKNOWN, reason=reason, **kw)


# ---------------------------------------------------------------------------
# WRITE: every transition, journaled.
# ---------------------------------------------------------------------------

def record_transition(journal, session_id: str, prior: Any, target: Any,
                      cause: str, evidence_ref: str, clock, logger=None):
    """Append one SESSION_TRANSITION. Goes through `append_scoped_event`, so
    the namespace invariant is enforced before persistence.

    EVERY transition is journaled, including the ones a no-trade day produces:
    the refusal that blocked entry, and the ordinary completion. A day that
    decided not to trade has to be able to prove it decided.
    """
    prior_v = getattr(prior, "value", str(prior))
    target_v = getattr(target, "value", str(target))
    return append_scoped_event(
        journal,
        session_scope_id(session_id),
        SESSION_EVENT_TYPE,
        f"{session_id}:{SESSION_EVENT_TYPE}:{prior_v}->{target_v}:{cause}",
        {"session_id": session_id, "prior_state": prior_v,
         "next_state": target_v, "cause": cause, "evidence_ref": evidence_ref},
        clock=clock, logger=logger)


def recorded_transitions(journal, session_id: str) -> List[Dict[str, Any]]:
    """This session's transitions, in journal order. Never raises."""
    try:
        events = journal.read_events(session_scope_id(session_id))
    except Exception:
        return []
    out = []
    for event in session_events(events):
        payload = dict(getattr(event, "payload", {}) or {})
        payload["recorded_at"] = getattr(event, "recorded_at", None)
        payload["sequence_no"] = getattr(event, "sequence_no", None)
        payload["event_id"] = getattr(event, "event_id", None)
        out.append(payload)
    return out


# ---------------------------------------------------------------------------
# READ: state derived from the journal, reconciled against broker truth.
# ---------------------------------------------------------------------------

def _state_from_transitions(transitions) -> Tuple[Any, str]:
    """Replay the recorded transitions. The last next_state is the state."""
    if not transitions:
        return UNKNOWN, "no session transitions recorded"
    last = transitions[-1]
    raw = last.get("next_state")
    try:
        return TradingSessionState(raw), f"journal: {raw} ({last.get('cause')})"
    except ValueError:
        return UNKNOWN, f"journal records an unrecognised state {raw!r}"


def _chain_is_contiguous(transitions) -> Optional[str]:
    """Each transition's prior_state must be the previous next_state.

    A gap means events are missing or interleaved from another session, and a
    state derived from a broken chain is a guess.
    """
    previous = None
    for i, t in enumerate(transitions):
        prior, nxt = t.get("prior_state"), t.get("next_state")
        if previous is not None and prior != previous:
            return (f"transition {i} claims prior_state {prior!r} but the "
                    f"previous transition ended at {previous!r} -- the recorded "
                    f"chain is broken, so no state can be derived from it")
        previous = nxt
    return None


def _position_evidence(journal) -> Tuple[bool, Tuple[str, ...], Optional[str]]:
    """(any_open_per_journal, open_symbols, error) from POSITION events only."""
    try:
        open_syms: List[str] = []
        for group_id in position_group_ids(journal):
            events = journal.read_events(group_id)
            if not events:
                continue
            state = fold(events)
            for coid, leg in state.legs.items():
                if net_quantity(leg) > 0:
                    open_syms.append(str(leg.contract_id or coid))
        return bool(open_syms), tuple(sorted(open_syms)), None
    except Exception as exc:  # noqa: BLE001 -- an unreadable journal is a finding
        return False, (), f"{type(exc).__name__}: {exc}"


def reconstruct(journal, session_id: str, broker_truth) -> SessionLifecycleState:
    """Rebuild session state from the journal, then reconcile it with the broker.

    `broker_truth` is a `bujji.broker_truth.BrokerTruth`, or None when no read
    was possible -- which is itself UNKNOWN.
    """
    evidence: Dict[str, Any] = {"session_id": session_id}

    transitions = recorded_transitions(journal, session_id)
    evidence["transitions"] = len(transitions)

    broken = _chain_is_contiguous(transitions)
    if broken:
        return _unknown(broken, session_id=session_id,
                        transitions=tuple(transitions), evidence=evidence)

    journal_state, why = _state_from_transitions(transitions)
    evidence["journal"] = why
    if journal_state == UNKNOWN:
        return _unknown(why, session_id=session_id, journal_state=None,
                        transitions=tuple(transitions), evidence=evidence)

    journal_open, journal_symbols, journal_error = _position_evidence(journal)
    evidence["journal_open_symbols"] = list(journal_symbols)
    if journal_error:
        evidence["journal_error"] = journal_error
        return _unknown(
            f"the position event history could not be read ({journal_error}) -- "
            f"a session cannot be certified against evidence it cannot open",
            session_id=session_id, journal_state=journal_state,
            transitions=tuple(transitions), evidence=evidence)

    if broker_truth is None or getattr(broker_truth, "is_unknown", True):
        detail = getattr(broker_truth, "detail", "no broker read was made")
        evidence["broker"] = f"UNKNOWN: {detail}"
        return _unknown(
            f"broker truth is UNKNOWN ({detail}) -- the journal says "
            f"{journal_state.value}, and nothing can confirm it",
            session_id=session_id, journal_state=journal_state,
            broker_state="UNKNOWN", transitions=tuple(transitions),
            evidence=evidence)

    broker_open = bool(getattr(broker_truth, "is_open", False))
    broker_symbols = tuple(getattr(broker_truth, "symbols", ()) or ())
    evidence["broker"] = getattr(broker_truth, "state", None)
    evidence["broker_symbols"] = list(broker_symbols)

    says_open = journal_state in _JOURNAL_SAYS_OPEN
    if says_open and not broker_open:
        return _unknown(
            f"the journal says {journal_state.value} but the broker reports no "
            f"open legs -- one of them is wrong and neither is assumed",
            session_id=session_id, journal_state=journal_state,
            broker_state=getattr(broker_truth, "state", None),
            transitions=tuple(transitions), evidence=evidence)
    if not says_open and broker_open:
        return _unknown(
            f"the broker holds {list(broker_symbols)} but the journal says "
            f"{journal_state.value} -- exposure this session does not believe "
            f"it has is the case reconciliation exists for",
            session_id=session_id, journal_state=journal_state,
            broker_state=getattr(broker_truth, "state", None),
            open_symbols=broker_symbols, transitions=tuple(transitions),
            evidence=evidence)

    return SessionLifecycleState(
        state=journal_state,
        reason=f"journal and broker agree ({journal_state.value})",
        session_id=session_id, journal_state=journal_state,
        broker_state=getattr(broker_truth, "state", None),
        open_symbols=broker_symbols, transitions=tuple(transitions),
        evidence=evidence)
