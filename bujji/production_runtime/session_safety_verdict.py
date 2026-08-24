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
    "HISTORICAL_UNRESOLVED_EXPOSURE":
        "the journal records open exposure from an earlier trading day that "
        "nothing has reconciled -- either it was closed and never recorded, or "
        "it is still live, and both need an operator",
    # Beside HISTORICAL_UNRESOLVED_EXPOSURE, and blindness for the same
    # reason. It covers two sub-cases -- the broker could not be read, or the
    # broker confirmed the orphan is still open -- and BOTH are states in
    # which this process cannot account for what the account holds. Classing
    # it as a disciplined decline would exit 0 on an account carrying exposure
    # Bujji never opened.
    "ORPHAN_EXPOSURE_UNRESOLVED":
        "the broker held a position no journal group claims and nothing has "
        "proved it closed -- either it is still live or its flatten was never "
        "confirmed, and only a broker-confirmed flat resolves it",
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


# REFUSALS THAT ARE DELIBERATELY NOT BLINDNESS.
#
# `_blindness_detail` returning None used to mean two different things: "this
# refusal is a disciplined decline made with full sight" and "nobody has
# classified this reason yet". Those are opposites, and only one of them is
# safe. A reason nobody classified reaches no operator at all.
#
# Membership here is the explicit statement that a refusal was considered and
# judged not to be evidence of blindness. `is_classified` requires every
# refusal to be in ONE of the two sets, so a new one cannot slip through by
# being absent from both.
_DELIBERATELY_NOT_BLINDNESS = {
    "STRATEGY_ALREADY_DEPLOYED_TODAY":
        "the day's one strategy was already deployed -- reached with full "
        "sight, exactly as 'no strategy fit today' is, so escalating it would "
        "page the operator after every restart of a completed trading day",
}


def is_classified(reason: str) -> bool:
    """True when a refusal reason has been deliberately classified, either as
    blindness or explicitly as not blindness."""
    return _blindness_detail(reason) is not None or reason in _DELIBERATELY_NOT_BLINDNESS


@dataclass(frozen=True)
class SessionSafetyVerdict:
    """`safe=False` must become a non-zero process exit, never a warning.

    PENDING_EVIDENCE IS A THIRD OUTCOME, and it is not a softer `safe`.
    `safe` says nothing went wrong. `pending_evidence` says the session's own
    evidence was never established -- a verification that had to run did not
    reach a verdict. A session that cannot prove what it saw is not a
    successful session, so it does not exit 0; but it is not the same claim as
    "something is wrong", and an operator reading the alert should not have to
    guess which they have.
    """

    safe: bool
    reasons: Tuple[str, ...]
    position_existed: bool
    pending_evidence: bool = False
    pending_reasons: Tuple[str, ...] = ()

    @property
    def certified(self) -> bool:
        """Safe AND provable. The only state that may exit 0."""
        return self.safe and not self.pending_evidence

    def as_dict(self) -> Dict[str, Any]:
        return {
            "safe": self.safe,
            "reasons": list(self.reasons),
            "position_existed": self.position_existed,
            "pending_evidence": self.pending_evidence,
            "pending_reasons": list(self.pending_reasons),
            "certified": self.certified,
        }


def _position_existed(summary: Dict[str, Any]) -> bool:
    for key in _POSITION_KEYS:
        if summary.get(key):
            return True
    # entry_filled is written on every entry attempt, True only on a real fill.
    return summary.get("entry_filled") is True


def _tick_evidence_findings(summary):
    """(unsafe_reasons, pending_reasons) from the tick journal's READ side.

    Before M2 was integrated, `tick_journal.faithful` was the WRITER stating
    its own drop count and nothing ever opened the file back up. These read
    the verdicts that `bujji.production_runtime.tick_evidence` records after
    actually reading the journals.
    """
    unsafe, pending = [], []

    # 1. What earlier processes left behind, inspected at startup.
    prior = summary.get("prior_tick_journals")
    if isinstance(prior, dict):
        if not prior.get("inspected"):
            pending.append(
                f"prior tick journals were never inspected "
                f"({prior.get('error') or 'inspection did not run'}) -- whether an "
                f"earlier process died mid-session was not established")
        for record in prior.get("corrupt") or ():
            unsafe.append(
                f"a prior tick journal is CORRUPT ({record.get('path')}) -- that "
                f"session's evidence can never be read, so nothing about it can "
                f"be reconstructed or certified")
    else:
        pending.append(
            "prior tick journals were never inspected -- the startup evidence "
            "check did not run at all")

    # 2. This session's own journal, verified by reading it back.
    journal = summary.get("tick_journal")
    if not isinstance(journal, dict):
        # NOT the same as "no journal was expected". The runner now always
        # records this key, stating `expected: False` when the configuration
        # has no feed. An absent key means the session never said -- so it
        # cannot be certified, though nothing is known to be wrong.
        pending.append(
            "the session recorded nothing about a tick journal at all -- not "
            "even that none was expected, so what it saw cannot be established")
        return unsafe, pending

    if journal.get("expected") is False:
        # Declared absent, legitimately: a replay or store-backed session
        # records no ticks. There is nothing to verify, and nothing missing.
        return unsafe, pending

    verified = journal.get("verified")
    if not isinstance(verified, dict):
        pending.append(
            "this session's tick journal was never read back -- `faithful` is "
            "the writer's own count, which a truncated or altered file also "
            "reports")
    elif verified.get("outcome") == "NOT_FAITHFUL":
        unsafe.append(
            f"the tick journal does not verify against its manifest "
            f"({verified.get('detail')}) -- the writer's counters and the file "
            f"on disk disagree, so the recorded evidence is not trustworthy")
    elif verified.get("outcome") != "VERIFIED":
        pending.append(
            f"the tick journal could not be verified ({verified.get('detail')}) "
            f"-- evidence for this session is not established")

    # 3. The post-session replay audit.
    replay = journal.get("replay")
    if not isinstance(replay, dict):
        pending.append(
            "the post-session replay audit never ran -- it is not known whether "
            "the recorded inputs reproduce")
    elif not replay.get("reproduced"):
        unsafe.append(
            f"the recorded tick inputs did not reproduce on replay "
            f"({replay.get('detail')}) -- the session's own evidence is not "
            f"self-consistent and cannot support certification")

    return unsafe, pending


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

    # -- TICK EVIDENCE, read back rather than self-reported. ---------------
    #
    # These apply WHETHER OR NOT a position was opened. A corrupt prior journal
    # means an earlier session can never be reconstructed, and a verification
    # that did not reach a verdict means this one cannot be certified. Neither
    # depends on today having taken risk.
    evidence_reasons, pending = _tick_evidence_findings(summary)
    truth_reasons.extend(evidence_reasons)

    # HISTORICAL UNRESOLVED EXPOSURE is UNSAFE, not merely pending. The
    # journal records exposure from an earlier day that nothing has reconciled
    # -- either it was closed and never recorded, or it is still live. Both
    # are conditions an operator must resolve, and neither may exit 0.
    historical = summary.get("historical_exposure")
    if isinstance(historical, dict):
        for group in historical.get("stale") or ():
            truth_reasons.append(
                f"historical unresolved exposure in {group.get('position_group_id')} "
                f"[{group.get('lifecycle_state')}, last event "
                f"{group.get('last_event_date')}] -- the account cannot be "
                f"reconciled against its own record until an operator resolves it")
        if not historical.get("inspected"):
            truth_reasons.append(
                f"historical exposure was never inspected "
                f"({historical.get('error') or 'the check did not run'}) -- "
                f"whether an earlier day left open exposure was not established")

    # UNRESOLVED ORPHAN EXPOSURE is UNSAFE, on the same reasoning as
    # historical exposure and with the same asymmetry: the broker held
    # something no journal group claims, and nothing has proved it gone. A
    # session that ends with one outstanding has not established that the
    # account is flat, whatever its own positions did.
    orphan = summary.get("orphan_exposure")
    if isinstance(orphan, dict):
        for record in orphan.get("unresolved") or ():
            truth_reasons.append(
                f"unresolved orphan exposure {record.get('symbol')} "
                f"x{record.get('signed_quantity')} "
                f"[{record.get('record_state')}, "
                f"broker={record.get('broker_truth_state') or 'UNREAD'}] -- the "
                f"broker held this and no journal group claims it; only a "
                f"broker-confirmed flat resolves it")
        if not orphan.get("inspected"):
            truth_reasons.append(
                f"orphan exposure was never inspected "
                f"({orphan.get('error') or 'the check did not run'}) -- whether "
                f"the account holds exposure Bujji never opened was not "
                f"established")

    had_position = _position_existed(summary)
    if not had_position:
        return SessionSafetyVerdict(
            safe=not truth_reasons, reasons=tuple(truth_reasons),
            position_existed=False,
            pending_evidence=bool(pending), pending_reasons=tuple(pending))

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

    # BLIND OPEN RISK IS UNSAFE, WHETHER OR NOT ANYTHING BROKE AFTERWARDS.
    #
    # A session that held an open position while its price path was invalid
    # was, for those cycles, carrying risk it could not see: no valuation, no
    # stop, no target, no adjustment. That is true regardless of whether the
    # blindness lasted long enough to arm the EMERGENCY_BLIND brake, and it
    # remains true if a later closure succeeded perfectly.
    #
    # WHY IT IS NOT MERELY "PENDING". Pending means the session could not
    # prove what it saw. This is stronger: the session positively recorded
    # that it could NOT see, while short. Grading that as a clean exit 0
    # would tell the operator a blind session and a sighted one are the same
    # outcome -- which is the reporting failure that let six blind hours on
    # 2026-08-21 look like an ordinary day.
    #
    # The distinction the runtime draws is preserved here rather than
    # flattened: transient invalidity suspends management and is recorded;
    # repeated invalidity arms the brake; EITHER, with a position open, means
    # this session is not certified.
    blind_cycles = summary.get("price_path_invalid") or ()
    if blind_cycles:
        reasons_seen = []
        for entry in blind_cycles:
            r = entry.get("reason") if isinstance(entry, dict) else str(entry)
            if r and r not in reasons_seen:
                reasons_seen.append(r)
        reasons.append(
            f"a position was open while the price path was INVALID for "
            f"{len(blind_cycles)} cycle(s) ({', '.join(reasons_seen) or 'unspecified'}) "
            f"-- risk was held without price visibility, so no stop, target or "
            f"adjustment could be evaluated for those cycles. This is not "
            f"certified regardless of how the session ended.")

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
    elif journal.get("expected") is False:
        # SAY THE TRUE THING. A session with no tick feed configured writes
        # `expected: False`, and nothing was interrupted -- there was simply
        # never a feed. Reporting it as "interrupted before sealing" sends an
        # operator looking for a crash that did not happen.
        #
        # It is still a refusal, and a serious one: a position was opened and
        # this session holds NO market evidence for it. Only the reason
        # changes, never the verdict.
        reasons.append(
            "a position was opened and NO tick feed was configured for this "
            "session, so no market evidence exists for the risk it held -- "
            "nothing was interrupted; there was never anything to seal")
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
        safe=not reasons, reasons=tuple(reasons), position_existed=True,
        pending_evidence=bool(pending), pending_reasons=tuple(pending))
