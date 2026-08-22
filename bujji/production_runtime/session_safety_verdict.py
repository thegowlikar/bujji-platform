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


# A REFUSAL TO TRADE IS NOT A FAULT. THESE REFUSALS ARE.
#
# Most of Bujji's no-trade days are the system working. The stability gate
# declines the large majority of cycles by design -- the production config
# says so in its own words: "NO_TRADE remains the EXPECTED majority verdict --
# that is the gate reading the market honestly, not a defect" -- and "no
# strategy for this regime" returns without recording any reason at all.
# Those paths write NOTHING, so they cannot reach this rule, and an operator
# is never woken for a quiet market.
#
# What DOES reach it is a session that got as far as the entry choke point and
# was turned away because it could not SEE: a dead feed, an unreadable
# account, a universe that cannot cover the band, a book too old to price.
#
# THIS WIDENS THIS MODULE'S QUESTION, deliberately, from "did this session
# prove the book is closed?" to "is this session's silence trustworthy?" The
# justification is the same one already accepted for
# `position_truth_established` below: a session that opened nothing because it
# was blind has not thereby proven anything. Its silence is not evidence.
_BLINDNESS_REFUSALS = {
    "SELECTION_BAND":
        "the eligible selection band could not be determined from the chain",
    "BAND_NOT_SUBSCRIBED":
        "contracts the strategy could have chosen were never subscribed -- a "
        "configuration mismatch that cannot be satisfied at runtime",
    "BAND_COVERAGE":
        "the contracts the strategy would have chosen from had no fresh ticks",
    "POSITION_RECONCILIATION":
        "the broker's account could not be read before entry",
    # NOTE what is deliberately ABSENT here: STRATEGY_ALREADY_DEPLOYED_TODAY.
    # That refusal is reached with full sight -- the journal was read, and it
    # said the day's one strategy was already deployed by an earlier process.
    # A disciplined decline is not blindness and must not exit non-zero, or
    # every restart-after-a-completed-trade would page the operator.
    "PRIOR_FILLS_UNREADABLE":
        "the position group journal could not be read, so whether this "
        "account already traded today was never established",
    "DATA_QUALITY_NOT_ASSESSED":
        "a market snapshot should have been graded and was not",
    "STALE_MARKET_DATA":
        "the option chain was too old to construct an order from",
}

# `DATA_QUALITY_<quality>` is composed at runtime from the verdict's own
# label, so the exact strings cannot be enumerated here. Graded-and-refused
# is a data-path failure whatever the label says.
_BLINDNESS_PREFIXES = ("DATA_QUALITY_",)


def _blindness_detail(reason: str):
    """The operator-facing explanation for a refusal, or None if this refusal
    is not evidence of blindness."""
    if reason in _BLINDNESS_REFUSALS:
        return _BLINDNESS_REFUSALS[reason]
    for prefix in _BLINDNESS_PREFIXES:
        if reason.startswith(prefix):
            return (f"the market-data quality gate graded the snapshot and refused "
                    f"({reason})")
    return None


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

    # POSITION TRUTH IS UNSAFE TO LACK EVEN WITH NO LOCAL POSITION.
    #
    # `position_truth_established` is written by the entry choke point: True
    # once a reconciliation has run, False when one was attempted and could
    # not establish what the broker holds. An explicit False means an entry
    # was considered while this process could not see the account -- so the
    # broker may be holding exposure nothing is managing, and the fact that
    # THIS process opened nothing is not evidence of safety.
    #
    # Checked BEFORE the no-position early return, and keyed on an explicit
    # False rather than a missing key: a session that never reached an entry
    # decision has no opinion to record, and firing on every quiet day would
    # train the operator to ignore the alarm.
    truth_reasons = []
    if summary.get("position_truth_established") is False:
        truth_reasons.append(
            "position truth could not be established -- an entry was considered "
            "while the broker's account could not be read, so exposure this "
            "process is not managing may exist")

    # A SESSION THAT NEVER SAW THE MARKET DID NOT DECLINE TO TRADE -- IT
    # COULD NOT. Accumulated across every cycle, because `entry_blocked_by`
    # alone is last-write-wins and a blind cycle 5 is invisible behind a
    # different refusal at cycle 90.
    recorded = summary.get("entry_blocked_reasons")
    if isinstance(recorded, (list, tuple)):
        for reason in recorded:
            detail = _blindness_detail(str(reason))
            if detail:
                truth_reasons.append(
                    f"entry was refused ({reason}): {detail} -- this session was "
                    f"blind, and a blind session's silence is not evidence that "
                    f"nothing needed doing")

    had_position = _position_existed(summary)
    if not had_position:
        return SessionSafetyVerdict(
            safe=not truth_reasons, reasons=tuple(truth_reasons),
            position_existed=False)

    reasons = list(truth_reasons)

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

    # A POSITION WITHOUT EVIDENCE CANNOT BE EXPLAINED AFTERWARDS.
    #
    # A session that opened risk and cannot produce a sealed, faithful tick
    # journal has no reconstructable account of what it saw when it acted. That
    # is not a record-keeping inconvenience -- every later question about the
    # decision (was the book fresh? did the feed go quiet? what did the leg
    # price at?) becomes unanswerable, and "we cannot tell" about an open
    # position is exactly what this module refuses to report as clean.
    #
    # UNSEALED means the process was killed before it could seal: SIGKILL,
    # power loss, an OOM kill. `_shutdown()` runs from a `finally` and covers
    # orderly termination only -- it cannot cover those, and no handler does.
    # A missing journal entry is therefore treated as absent evidence, not as
    # an absent problem.
    journal = summary.get("tick_journal")
    if not isinstance(journal, dict):
        reasons.append(
            "a position was opened and no tick journal was recorded at all -- "
            "the session cannot account for the market it acted on")
    elif not journal.get("sealed"):
        reasons.append(
            f"a position was opened and the tick journal was never sealed "
            f"({journal.get('error') or 'no manifest written'}) -- the session "
            f"was interrupted before it could state what it had captured")
    elif not journal.get("faithful"):
        reasons.append(
            f"a position was opened and the tick journal is not faithful "
            f"(dropped={journal.get('dropped')}, offered={journal.get('offered')}, "
            f"written={journal.get('written')}) -- evidence for this session is "
            f"incomplete and cannot support a claim that it went well")

    if summary.get("has_unresolved_exit"):
        unresolved = summary.get("exits_unresolved") or []
        reasons.append(
            f"{len(unresolved)} exit(s) never resolved -- their true state at the "
            f"broker is unknown, so the position cannot be called closed")

    return SessionSafetyVerdict(
        safe=not reasons, reasons=tuple(reasons), position_existed=True)
