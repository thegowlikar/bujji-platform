"""Exposure from an earlier day is a recovery state, not a margin crash.

On 2026-08-21 a session entered a two-leg position and flattened the account.
The exit was never journaled -- `eod_closure` wrote nothing -- so
`PG-96e5dc16a83f204c31bb` folds OPEN forever with both legs ACKED at net 65.
The account is flat; the record says it is not.

What that produced was not an error message. `read_all_group_ids` has no date
scope, the runner passes empty contract maps, `project_whole_book_to_margin_legs`
raises on legs it cannot map, and the session reported "margin_snapshot
unavailable". The refusal was correct. Its SHAPE was the defect: a
margin-subsystem crash naming nothing an operator could act on, arrived at by
accident.
"""
from __future__ import annotations

import datetime as _dt
import logging
import pathlib
import shutil
import tempfile

import pytest

from bujji.broker_truth import flat, open_with, unknown
from bujji.broker_truth.models import OpenLeg
from bujji.core.clock import IST
from bujji.journal.position_group_journal import PositionGroupJournal
from bujji.production_runtime.historical_exposure import (
    CORRECTION_EVENT, RESOLUTION_KEY, inspect)

LOG = logging.getLogger("test")
LIVE = pathlib.Path("/opt/bujji/app/data/options_os_trading_journal.db")
CE = "NSE:NIFTY2690124500CE"
TODAY = "2026-08-24"
YESTERDAY = "2026-08-21"


def _clock(day):
    d = _dt.date.fromisoformat(day)
    return lambda: _dt.datetime(d.year, d.month, d.day, 9, 30, tzinfo=IST)


def _open_group(tmp_path, day, name="h.db", group="PG-OLD"):
    j = PositionGroupJournal(str(tmp_path / name))
    clock = _clock(day)
    coid = f"{group}-LEG-0"
    j.append_event(group, "MINTED", f"{group}:M",
                   {"plan_id": "p", "strategy_id": "S", "underlying": "NIFTY"}, clock=clock)
    j.append_event(group, "CONSTRUCTED", f"{group}:C", {
        "contract_client_order_map": {CE: coid}, "requested_quantities": {coid: 65},
        "actions": {coid: "SELL"}, "target_position_group_ids": {},
        "target_contract_ids": {}, "flip_link_ids": {}}, clock=clock)
    j.append_event(group, "SUBMIT_INTENT", f"{group}:SI", {"client_order_id": coid}, clock=clock)
    j.append_event(group, "SUBMIT_ACK", f"{group}:SA",
                   {"client_order_id": coid, "broker_order_id": "B",
                    "broker_reported_status": "FILLED"}, clock=clock)
    j.append_event(group, "FILL_OBSERVED", f"{group}:F", {
        "client_order_id": coid, "cumulative_filled_quantity_after": 65,
        "cumulative_average_fill_price_after": 23.1, "delta_quantity": 65,
        "delta_value": 1501.5, "delta_cost_basis_status": "DERIVED",
        "fill_price": 23.1}, clock=clock)
    return j, group


def _correction(key, value):
    """An OPERATOR_CORRECTION_RECORDED payload with the fields the journal
    requires -- a named person, a stated reason, and what they checked."""
    return {key: value,
            "correcting_event_type": "FILL_OBSERVED",
            "correcting_payload": {},
            "operator_id": "operator@bujji",
            "justification": "reconciled against the broker's own trade book",
            "evidence_reference": "fyers tradebook 2026-08-21",
            "reviewed_at": "2026-08-24T09:00:00+05:30"}


_FLAT = flat("no open legs", "paper", True)
_OPEN = open_with([OpenLeg(CE, 65)], "held", "paper", True)


# --------------------------------------------------------------------------
# Detection.
# --------------------------------------------------------------------------

def test_a_group_left_open_by_an_earlier_day_is_detected(tmp_path):
    j, group = _open_group(tmp_path, YESTERDAY)
    report = inspect(j, TODAY, _FLAT, LOG)

    assert report.inspected
    assert [g.position_group_id for g in report.stale] == [group]
    assert report.stale[0].last_event_date == YESTERDAY
    assert report.blocks_entry


def test_todays_own_open_position_is_not_historical(tmp_path):
    """A live position opened this session is not a recovery problem. A guard
    that blocks on it would stop every session that holds anything."""
    j, _g = _open_group(tmp_path, TODAY)
    report = inspect(j, TODAY, _OPEN, LOG)
    assert report.stale == []
    assert not report.blocks_entry


def test_a_closed_group_from_an_earlier_day_is_not_stale(tmp_path):
    """Only ACTIVE lifecycle states carry exposure."""
    j, group = _open_group(tmp_path, YESTERDAY)
    coid = f"{group}-LEG-0"
    j.append_event(group, "TARGET_GROUP_REDUCTION_APPLIED", f"{group}:R", {
        "source_client_order_id": coid, "source_position_group_id": group,
        "target_contract_id": CE, "reduced_quantity_delta": 65},
        clock=_clock(YESTERDAY))
    assert inspect(j, TODAY, _FLAT, LOG).stale == []


def test_an_empty_journal_blocks_nothing(tmp_path):
    report = inspect(PositionGroupJournal(str(tmp_path / "e.db")), TODAY, _FLAT, LOG)
    assert report.inspected and not report.blocks_entry


# --------------------------------------------------------------------------
# The operator path.
# --------------------------------------------------------------------------

def test_the_instructions_name_the_group_the_legs_and_the_date(tmp_path):
    """An operator must be able to act from the message alone."""
    j, group = _open_group(tmp_path, YESTERDAY)
    text = inspect(j, TODAY, _FLAT, LOG).operator_instructions()

    assert group in text
    assert YESTERDAY in text
    assert CE in text
    assert CORRECTION_EVENT in text
    assert RESOLUTION_KEY in text
    assert "operator_id" in text and "evidence_reference" in text, (
        "the instructions must name the fields the journal requires")
    assert "CONFIRMED_FLAT" in text
    assert "Do NOT assume" in text, (
        "a flat account today is what has to be proven, not the proof")


def test_an_operator_correction_stops_the_block(tmp_path):
    j, group = _open_group(tmp_path, YESTERDAY)
    assert inspect(j, TODAY, _FLAT, LOG).blocks_entry

    j.append_event(group, CORRECTION_EVENT, f"{group}:OC",
                   _correction(RESOLUTION_KEY, True), clock=_clock(TODAY))

    report = inspect(j, TODAY, _FLAT, LOG)
    assert report.stale == []
    assert report.resolved == [group]
    assert not report.blocks_entry


def test_a_correction_without_the_resolution_marker_does_not_clear_it(tmp_path):
    """A correction that says something else about the group is not a
    statement that the exposure was reconciled."""
    j, group = _open_group(tmp_path, YESTERDAY)
    j.append_event(group, CORRECTION_EVENT, f"{group}:OC2",
                   _correction("note", "looked at this"), clock=_clock(TODAY))
    assert inspect(j, TODAY, _FLAT, LOG).blocks_entry


def test_the_correction_leaves_the_forensic_trail_intact(tmp_path):
    """OPERATOR_CORRECTION_RECORDED is audit-only by design: it mutates no
    leg, no lifecycle state, no closure reason. Clearing the group silently
    would erase the only durable trace that an exit went unrecorded."""
    from bujji.trading_brain.risk_governor.position_group_fold import fold

    j, group = _open_group(tmp_path, YESTERDAY)
    before = fold(j.read_events(group))
    j.append_event(group, CORRECTION_EVENT, f"{group}:OC",
                   _correction(RESOLUTION_KEY, True), clock=_clock(TODAY))
    after = fold(j.read_events(group))

    assert after.lifecycle_state == before.lifecycle_state == "OPEN"
    assert len(after.legs) == len(before.legs)


# --------------------------------------------------------------------------
# Fail closed.
# --------------------------------------------------------------------------

def test_an_inspection_that_could_not_run_blocks(tmp_path):
    class _Broken:
        def read_all_group_ids(self):
            raise OSError("database disk image is malformed")

    report = inspect(_Broken(), TODAY, _FLAT, LOG)
    assert not report.inspected
    assert report.error
    assert report.blocks_entry, "a check that did not run is not a clean result"


def test_a_broker_unknown_does_not_hide_the_exposure(tmp_path):
    j, group = _open_group(tmp_path, YESTERDAY)
    report = inspect(j, TODAY, unknown("socket closed", "paper"), LOG)
    assert report.blocks_entry
    assert "UNKNOWN" in report.operator_instructions() or report.broker_state == "UNKNOWN"


# --------------------------------------------------------------------------
# The real journal.
# --------------------------------------------------------------------------

@pytest.mark.skipif(not LIVE.exists(), reason="live journal not present")
def test_the_real_2026_08_21_group_is_reported_as_a_recovery_state(tmp_path):
    """Against a COPY of the production journal. The group is forensic
    evidence of an exit that was never recorded; it is named, not deleted."""
    work = tmp_path / "live.db"
    shutil.copy(LIVE, work)
    report = inspect(PositionGroupJournal(str(work)), TODAY, _FLAT, LOG)

    assert report.blocks_entry
    ids = [g.position_group_id for g in report.stale]
    assert "PG-96e5dc16a83f204c31bb" in ids
    text = report.operator_instructions()
    assert "PG-96e5dc16a83f204c31bb" in text and "2026-08-21" in text


# --------------------------------------------------------------------------
# The verdict.
# --------------------------------------------------------------------------

def _verdict(summary):
    from bujji.production_runtime.session_safety_verdict import evaluate_session_safety
    return evaluate_session_safety(summary)


_CLEAN = {
    "prior_tick_journals": {"inspected": True, "unsealed": [], "corrupt": []},
    "tick_journal": {"expected": False, "detail": "no feed"},
}


def test_stale_exposure_makes_the_session_unsafe():
    summary = {"entry_filled": False, **_CLEAN,
               "historical_exposure": {
                   "inspected": True, "resolved": [], "blocks_entry": True,
                   "stale": [{"position_group_id": "PG-OLD", "lifecycle_state": "OPEN",
                              "last_event_date": YESTERDAY}]}}
    v = _verdict(summary)
    assert not v.safe
    assert any("PG-OLD" in r for r in v.reasons)


def test_an_uninspected_history_makes_the_session_unsafe():
    summary = {"entry_filled": False, **_CLEAN,
               "historical_exposure": {"inspected": False, "stale": [],
                                       "resolved": [], "error": "boom",
                                       "blocks_entry": True}}
    assert not _verdict(summary).safe


def test_a_clean_history_certifies():
    summary = {"entry_filled": False, **_CLEAN,
               "historical_exposure": {"inspected": True, "stale": [],
                                       "resolved": [], "blocks_entry": False}}
    assert _verdict(summary).certified
