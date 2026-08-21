"""Does this session's own summary prove the position is safely closed?

WHY THIS EXISTS.

Every fact this module reads was ALREADY computed, ALREADY correct, and
ALREADY logged at CRITICAL by the runner. What did not exist was any path
from those facts to the process exit code -- so a session could end with

    closure_reason        = CRITICAL_UNFLATTENED_POSITION
    canonical_close_outcome = NEVER_EXITED
    emergency_close_execution_error = "NameError: ..."
    final_positions_status  = UNKNOWN

and still `return EXIT_OK`. systemd saw success, `OnFailure=` never fired,
`bujji-alert@` never ran, ALERTS.jsonl gained no line and the operator's
phone stayed silent. The only trace was a log line nothing reads.

That is exactly what happened on 2026-08-21. An alert DID fire that day --
but only at 15:54:56, because an unrelated websocket hang got the process
SIGTERM-killed 21 minutes after it had logged SHUTDOWN. Had the process
exited cleanly, as it normally would, an unflattened option position would
have produced a green unit and total silence. The alarm fired by luck.

This module adds NO new detection. It reads the summary the runner already
builds and answers one question: did this session prove the book is closed?
Fail-closed by construction -- a position that existed and cannot be proven
flat is UNSAFE, because UNKNOWN is not FLAT.

Pure and side-effect free so it can be tested exhaustively without a broker,
a clock, or a session.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Tuple

# A position that was never opened cannot be left open. Every rule below is
# gated on this: a session that placed no trade is safe by construction.
_POSITION_KEYS = ("canonical_position_id",)

# `final_positions_status` is the closure machine's own broker read, already
# three-valued on purpose (runner: "When it could not establish truth, the
# positions are reported as UNKNOWN rather than as empty"). Only FLAT proves
# the book is closed.
_PROVES_FLAT = "FLAT"


@dataclass(frozen=True)
class SessionSafetyVerdict:
    """`safe=False` must become a non-zero process exit, never a warning."""

    safe: bool
    reasons: Tuple[str, ...]
    position_existed: bool

    def as_dict(self) -> Dict[str, Any]:
        return {
            "safe": self.safe,
            "reasons": list(self.reasons),
            "position_existed": self.position_existed,
        }


def _position_existed(summary: Dict[str, Any]) -> bool:
    for key in _POSITION_KEYS:
        if summary.get(key):
            return True
    # entry_filled is written on every entry attempt, True only on a real fill.
    return summary.get("entry_filled") is True


def evaluate_session_safety(summary: Dict[str, Any]) -> SessionSafetyVerdict:
    """Grade a finished session's summary. Never raises, never mutates.

    A missing key is treated as UNKNOWN, and UNKNOWN about an open position
    is UNSAFE. That asymmetry is deliberate: the cost of a false alarm is one
    push notification, and the cost of a missed one is an unmanaged naked
    option position held overnight.
    """
    if not isinstance(summary, dict):
        # A runner that returned something un-gradeable is itself a failure.
        return SessionSafetyVerdict(
            safe=False,
            reasons=(f"session summary is {type(summary).__name__}, not a dict -- "
                     "the session cannot be graded and must not be reported clean",),
            position_existed=False,
        )

    had_position = _position_existed(summary)
    if not had_position:
        return SessionSafetyVerdict(safe=True, reasons=(), position_existed=False)

    reasons = []

    status = summary.get("final_positions_status")
    if status != _PROVES_FLAT:
        reasons.append(
            f"a position was opened and final_positions_status={status!r} -- only "
            f"{_PROVES_FLAT!r} proves the book is closed")

    if summary.get("closure_reason") == "CRITICAL_UNFLATTENED_POSITION":
        reasons.append(
            "closure_reason=CRITICAL_UNFLATTENED_POSITION -- the closure machine "
            "itself reported the position was not confirmed closed")

    # Written by _assert_closure_truths_agree(). Explicitly False means the
    # broker, the lifecycle record and the archived summary disagree.
    if summary.get("closure_truths_agree") is False:
        divergences = summary.get("closure_truth_divergences") or []
        reasons.append(
            f"closure truths disagree ({len(divergences)}): "
            f"{'; '.join(str(d) for d in divergences) or 'unspecified'}")

    if summary.get("emergency_close_execution_error"):
        reasons.append(
            f"the emergency brake fired and FAILED to execute "
            f"({summary['emergency_close_execution_error']}) -- the brake is the "
            f"last line before an unmanaged position")

    # The brake ran and the broker did not confirm flat afterwards.
    if summary.get("emergency_close_reason") and \
            summary.get("emergency_close_broker_flat") is not True:
        reasons.append(
            f"an emergency close was triggered ({summary['emergency_close_reason']}) "
            f"and the broker did not confirm flat "
            f"(emergency_close_broker_flat={summary.get('emergency_close_broker_flat')!r})")

    if summary.get("has_unresolved_exit"):
        unresolved = summary.get("exits_unresolved") or []
        reasons.append(
            f"{len(unresolved)} exit(s) never resolved -- their true state at the "
            f"broker is unknown, so the position cannot be called closed")

    return SessionSafetyVerdict(
        safe=not reasons, reasons=tuple(reasons), position_existed=True)
