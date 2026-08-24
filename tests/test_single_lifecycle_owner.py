"""One owner for POSITION_ACTIVE, and it survives a restart.

Bujji ran TWO session-scoped state machines. `RuntimeState` and
`TradingSessionState` BOTH transitioned to POSITION_ACTIVE, from different call
sites, with no defined relationship -- and BOTH gated entry, RuntimeState
through `_ENTRY_ACCEPTING_STATES` and TradingSessionState through
`entry_control.can_enter_trade`. Neither was journaled, so neither survived a
restart.

TradingSessionState is now the single owner. Its transitions are journaled into
the same durable stream as position lifecycle, and state is DERIVED from that
stream reconciled against broker truth.
"""
from __future__ import annotations

import ast
import datetime as _dt
import inspect
import logging
import pathlib

import pytest

from bujji.broker_truth import STATE_CONFIRMED_FLAT, STATE_CONFIRMED_OPEN, flat, open_with, unknown
from bujji.broker_truth.models import OpenLeg
from bujji.core.clock import IST
from bujji.journal.position_group_journal import PositionGroupJournal
from bujji.production_runtime.session_lifecycle import (
    UNKNOWN, reconstruct, record_transition, recorded_transitions)
from bujji.production_runtime.trading_session_governor.session_trading_state import (
    TradingSessionState as TSS)

REPO = pathlib.Path(__file__).resolve().parent.parent
CLOCK = lambda: _dt.datetime(2026, 8, 24, 9, 30, tzinfo=IST)   # noqa: E731
LOG = logging.getLogger("test")
SESSION = "S-2026-08-24"
CE = "NSE:NIFTY2690124500CE"


def _journal(tmp_path, name="lc.db"):
    return PositionGroupJournal(str(tmp_path / name))


def _walk(journal, *pairs, session=SESSION):
    """Journal a chain of transitions."""
    for prior, nxt, cause in pairs:
        record_transition(journal, session, prior, nxt, cause=cause,
                          evidence_ref=f"ref:{cause}", clock=CLOCK, logger=LOG)


def _entered(journal, group="PG-1", qty=65, symbol=CE):
    coid = f"{group}-LEG-0"
    journal.append_event(group, "MINTED", f"{group}:M",
                         {"plan_id": "p", "strategy_id": "S", "underlying": "NIFTY"}, clock=CLOCK)
    journal.append_event(group, "CONSTRUCTED", f"{group}:C", {
        "contract_client_order_map": {symbol: coid},
        "requested_quantities": {coid: qty}, "actions": {coid: "SELL"},
        "target_position_group_ids": {}, "target_contract_ids": {},
        "flip_link_ids": {}}, clock=CLOCK)
    journal.append_event(group, "SUBMIT_INTENT", f"{group}:SI", {"client_order_id": coid}, clock=CLOCK)
    journal.append_event(group, "SUBMIT_ACK", f"{group}:SA",
                         {"client_order_id": coid, "broker_order_id": "B",
                          "broker_reported_status": "FILLED"}, clock=CLOCK)
    journal.append_event(group, "FILL_OBSERVED", f"{group}:F", {
        "client_order_id": coid, "cumulative_filled_quantity_after": qty,
        "cumulative_average_fill_price_after": 23.1, "delta_quantity": qty,
        "delta_value": 23.1 * qty, "delta_cost_basis_status": "DERIVED",
        "fill_price": 23.1}, clock=CLOCK)
    return group


_OPEN = open_with([OpenLeg(CE, 65)], "held", "paper", True)
_FLAT = flat("no open legs", "paper", True)
_UNKNOWN = unknown("socket closed", "paper")


# ==========================================================================
# SOLE OWNERSHIP OF POSITION_ACTIVE
# ==========================================================================

def _runtime_module_src(name):
    return (REPO / name).read_text()


def test_only_one_runtime_component_transitions_to_position_active():
    """THE proof. Every enabled-runtime write of POSITION_ACTIVE, found as AST
    rather than by grepping text."""
    owners = []
    for path in list((REPO / "bujji").rglob("*.py")) + [REPO / "bujji_options_os_runner.py"]:
        rel = str(path.relative_to(REPO))
        if "/tests/" in rel or rel.startswith("tests/"):
            continue
        try:
            tree = ast.parse(path.read_text())
        except Exception:
            continue
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            if node.func.attr not in ("transition", "_transition"):
                continue
            seg = ast.unparse(node)
            if "POSITION_ACTIVE" in seg:
                owners.append(f"{rel}:{node.lineno} {seg[:80]}")

    modules = {o.split(":")[0] for o in owners}
    assert modules <= {"bujji/production_runtime/trading_session_governor/session_governor.py"}, (
        f"more than one component transitions POSITION_ACTIVE: {owners}")


def test_runtimestate_no_longer_transitions_position_active():
    src = _runtime_module_src("bujji/production_runtime/trading_brain_runtime.py")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            seg = ast.unparse(node)
            assert not ("RuntimeState.POSITION_ACTIVE" in seg
                        and node.func.attr == "transition"), (
                f"RuntimeState still owns POSITION_ACTIVE: {seg}")


def test_the_second_entry_gate_is_retired():
    """`_ENTRY_ACCEPTING_STATES` decided the same question as
    `can_enter_trade`, from a different machine, and could disagree.

    Watches the RETIRED name too. A negative control caught this: reviving the
    gate under its retirement name passed, because the test only knew the
    original one. A constant kept visible for blame must not be readable.
    """
    src = _runtime_module_src("bujji/production_runtime/trading_brain_runtime.py")
    tree = ast.parse(src)
    retired_names = {"_ENTRY_ACCEPTING_STATES", "_ENTRY_ACCEPTING_STATES__RETIRED_M4"}
    live_reads = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in retired_names:
            if isinstance(node.ctx, ast.Store):
                continue          # the retirement definition itself
            live_reads.append(f"line {node.lineno}: {node.id}")
    assert live_reads == [], f"the retired gate still has a reader: {live_reads}"


def test_every_governor_transition_goes_through_the_journaled_path():
    import bujji.production_runtime.trading_session_governor.session_governor as mod

    tree = ast.parse(inspect.getsource(mod))
    direct = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "transition"
                and "_state_tracker" in ast.unparse(node.func)):
            direct.append(node.lineno)
    # Exactly one: the delegation inside `_transition` itself.
    assert len(direct) == 1, (
        f"{len(direct)} direct tracker transitions bypass the journal: {direct}")


# ==========================================================================
# TRANSITIONS ARE JOURNALED
# ==========================================================================

def test_a_transition_is_durable_with_cause_and_evidence(tmp_path):
    j = _journal(tmp_path)
    _walk(j, (TSS.INITIALIZING, TSS.ANALYSING_MARKET, "session_start"))

    recorded = recorded_transitions(j, SESSION)
    assert len(recorded) == 1
    t = recorded[0]
    assert t["session_id"] == SESSION
    assert t["prior_state"] == "INITIALIZING"
    assert t["next_state"] == "ANALYSING_MARKET"
    assert t["cause"] == "session_start"
    assert t["evidence_ref"] == "ref:session_start"
    assert t["recorded_at"] is not None


def test_a_no_trade_day_still_has_durable_history(tmp_path):
    """A day that decided not to trade must be able to prove it decided."""
    j = _journal(tmp_path)
    _walk(j,
          (TSS.INITIALIZING, TSS.ANALYSING_MARKET, "session_start"),
          (TSS.ANALYSING_MARKET, TSS.SESSION_COMPLETE, "no_strategy_fit"))

    recorded = recorded_transitions(j, SESSION)
    assert [t["next_state"] for t in recorded] == ["ANALYSING_MARKET", "SESSION_COMPLETE"]
    from bujji.production_runtime.position_group_scope import position_group_ids
    assert position_group_ids(j) == [], "a no-trade day invented no position group"


def test_a_refusal_is_journaled(tmp_path):
    j = _journal(tmp_path)
    _walk(j,
          (TSS.INITIALIZING, TSS.ANALYSING_MARKET, "session_start"),
          (TSS.ANALYSING_MARKET, TSS.SESSION_COMPLETE, "entry_refused:POSITION_RECONCILIATION"))
    assert "entry_refused" in recorded_transitions(j, SESSION)[-1]["cause"]


# ==========================================================================
# RESTART RECONSTRUCTION
# ==========================================================================

def test_restart_rebuilds_state_when_journal_and_broker_agree(tmp_path):
    j = _journal(tmp_path)
    _walk(j, (TSS.INITIALIZING, TSS.ANALYSING_MARKET, "session_start"),
          (TSS.ANALYSING_MARKET, TSS.STRATEGY_LOCKED, "locked"),
          (TSS.STRATEGY_LOCKED, TSS.POSITION_ACTIVE, "entry_filled"))
    _entered(j)

    got = reconstruct(j, SESSION, _OPEN)
    assert got.state is TSS.POSITION_ACTIVE
    assert not got.is_unknown
    assert not got.permits_entry


def test_restart_after_a_flat_exit_rebuilds_exited(tmp_path):
    j = _journal(tmp_path)
    _walk(j, (TSS.INITIALIZING, TSS.ANALYSING_MARKET, "s"),
          (TSS.ANALYSING_MARKET, TSS.STRATEGY_LOCKED, "l"),
          (TSS.STRATEGY_LOCKED, TSS.POSITION_ACTIVE, "f"),
          (TSS.POSITION_ACTIVE, TSS.EXITED, "exit_confirmed"))

    got = reconstruct(j, SESSION, _FLAT)
    assert got.state is TSS.EXITED
    assert got.already_deployed, "the day's strategy was deployed and must not repeat"
    assert not got.permits_entry


def test_only_strategy_locked_permits_entry(tmp_path):
    j = _journal(tmp_path)
    _walk(j, (TSS.INITIALIZING, TSS.ANALYSING_MARKET, "s"),
          (TSS.ANALYSING_MARKET, TSS.STRATEGY_LOCKED, "l"))
    got = reconstruct(j, SESSION, _FLAT)
    assert got.state is TSS.STRATEGY_LOCKED
    assert got.permits_entry, "a guard that never permits is not a guard"


# -- the five UNKNOWN paths ------------------------------------------------

def test_missing_history_is_unknown_and_blocks(tmp_path):
    got = reconstruct(_journal(tmp_path), SESSION, _FLAT)
    assert got.is_unknown and not got.permits_entry
    assert "no session transitions" in got.reason


def test_a_broken_transition_chain_is_unknown(tmp_path):
    """Events missing or interleaved. A state derived from a broken chain is
    a guess."""
    j = _journal(tmp_path)
    _walk(j, (TSS.INITIALIZING, TSS.ANALYSING_MARKET, "s"),
          (TSS.STRATEGY_LOCKED, TSS.POSITION_ACTIVE, "f"))   # gap: nothing reached STRATEGY_LOCKED
    got = reconstruct(j, SESSION, _OPEN)
    assert got.is_unknown and "chain is broken" in got.reason


def test_journal_says_open_but_broker_is_flat_is_unknown(tmp_path):
    j = _journal(tmp_path)
    _walk(j, (TSS.INITIALIZING, TSS.ANALYSING_MARKET, "s"),
          (TSS.ANALYSING_MARKET, TSS.STRATEGY_LOCKED, "l"),
          (TSS.STRATEGY_LOCKED, TSS.POSITION_ACTIVE, "f"))
    got = reconstruct(j, SESSION, _FLAT)
    assert got.is_unknown and not got.permits_entry
    assert "no open legs" in got.reason


def test_broker_holds_what_the_journal_does_not_know_about_is_unknown(tmp_path):
    """Exposure this session does not believe it has -- the case
    reconciliation exists for."""
    j = _journal(tmp_path)
    _walk(j, (TSS.INITIALIZING, TSS.ANALYSING_MARKET, "s"),
          (TSS.ANALYSING_MARKET, TSS.STRATEGY_LOCKED, "l"))
    got = reconstruct(j, SESSION, _OPEN)
    assert got.is_unknown and not got.permits_entry
    assert CE in got.reason


def test_broker_unknown_is_unknown(tmp_path):
    j = _journal(tmp_path)
    _walk(j, (TSS.INITIALIZING, TSS.ANALYSING_MARKET, "s"),
          (TSS.ANALYSING_MARKET, TSS.STRATEGY_LOCKED, "l"))
    got = reconstruct(j, SESSION, _UNKNOWN)
    assert got.is_unknown and not got.permits_entry
    assert "UNKNOWN" in got.reason


def test_no_broker_read_at_all_is_unknown(tmp_path):
    j = _journal(tmp_path)
    _walk(j, (TSS.INITIALIZING, TSS.ANALYSING_MARKET, "s"))
    assert reconstruct(j, SESSION, None).is_unknown


def test_a_corrupt_event_history_is_unknown(tmp_path):
    """An unreadable journal is not an empty one."""
    class _Corrupt:
        def read_events(self, gid):
            if str(gid).startswith("SESSION:"):
                class _E:
                    event_type = "SESSION_TRANSITION"
                    payload = {"session_id": SESSION, "prior_state": "INITIALIZING",
                               "next_state": "STRATEGY_LOCKED", "cause": "l",
                               "evidence_ref": "r"}
                    recorded_at = CLOCK()
                    sequence_no = 0
                    event_id = "e"
                return [_E()]
            raise OSError("database disk image is malformed")

        def read_all_group_ids(self):
            return ["PG-1"]

    got = reconstruct(_Corrupt(), SESSION, _FLAT)
    assert got.is_unknown and "could not be read" in got.reason


def test_an_unrecognised_recorded_state_is_unknown(tmp_path):
    j = _journal(tmp_path)
    j.append_event("SESSION:" + SESSION, "SESSION_TRANSITION", f"{SESSION}:weird",
                   {"session_id": SESSION, "prior_state": "INITIALIZING",
                    "next_state": "NOT_A_REAL_STATE", "cause": "c",
                    "evidence_ref": "r"}, clock=CLOCK)
    got = reconstruct(j, SESSION, _FLAT)
    assert got.is_unknown and "unrecognised state" in got.reason


def test_unknown_is_not_a_member_of_the_state_enum():
    """So no transition table can accept it as a target and no caller can
    transition INTO it by mistake."""
    assert UNKNOWN not in {s.value for s in TSS}
