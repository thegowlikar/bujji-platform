"""A PENDING_EVIDENCE session must fail its unit and raise the same alarm.

`safe` means nothing went wrong. `certified` means safe AND provable. A session
that cannot prove what it saw is not a success, so it must not exit 0 -- and it
must not be able to suppress the alert that a non-zero exit fires.

systemd only fires `OnFailure=` on a NON-ZERO exit, and only treats an exit as
failure when it is not listed in `SuccessExitStatus=`. So both halves have to
hold: the code must be non-zero, and nothing may declare it a success.
"""
from __future__ import annotations

import pathlib
import re

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
DEPLOY = REPO_ROOT / "deploy"

# Observed on root@139.59.76.137, 2026-08-22:
#   systemctl cat bujji-options-os-trading.service
# The base unit is version-controlled in deploy/; the OnFailure wiring lives in
# a DEPLOYED-ONLY drop-in, recorded here because the repo cannot assert it.
DEPLOYED_DROPINS = {
    "onfailure.conf": "[Unit]\nOnFailure=bujji-alert@%n.service\n",
}
DEPLOYED_SUCCESS_EXIT_STATUS = ""     # none set -> only exit 0 is success


def _runner():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "runner_exitcodes", REPO_ROOT / "bujji_options_os_runner.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --------------------------------------------------------------------------
# The code itself.
# --------------------------------------------------------------------------

def test_pending_evidence_is_non_zero():
    """systemd fires OnFailure= only on a non-zero exit."""
    assert _runner().EXIT_PENDING_EVIDENCE != 0


def test_pending_evidence_is_distinct_from_every_other_outcome():
    mod = _runner()
    codes = {"OK": mod.EXIT_OK, "CONFIG": mod.EXIT_CONFIG_ERROR,
             "RUNTIME": mod.EXIT_RUNTIME_ERROR, "UNSAFE": mod.EXIT_UNSAFE_SESSION,
             "PENDING": mod.EXIT_PENDING_EVIDENCE}
    assert len(set(codes.values())) == len(codes), (
        f"exit codes collide, so an operator cannot tell outcomes apart: {codes}")


# --------------------------------------------------------------------------
# Nothing may declare it a success.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("unit", sorted(p.name for p in DEPLOY.glob("*.service")))
def test_no_unit_declares_the_pending_code_a_success(unit):
    """`SuccessExitStatus=4` anywhere would make systemd treat a session that
    could not prove itself as a clean run, and OnFailure= would never fire."""
    code = _runner().EXIT_PENDING_EVIDENCE
    text = (DEPLOY / unit).read_text()
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or not stripped.startswith("SuccessExitStatus"):
            continue
        declared = re.findall(r"\d+", stripped.split("=", 1)[1])
        assert str(code) not in declared, (
            f"{unit} declares exit {code} a success -- a PENDING_EVIDENCE "
            f"session would exit green and raise no alarm")


@pytest.mark.parametrize("unit", sorted(p.name for p in DEPLOY.glob("*.service")))
def test_no_unit_declares_the_unsafe_code_a_success(unit):
    """The same guarantee the pending code inherits from."""
    code = _runner().EXIT_UNSAFE_SESSION
    for line in (DEPLOY / unit).read_text().splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or not stripped.startswith("SuccessExitStatus"):
            continue
        assert str(code) not in re.findall(r"\d+", stripped.split("=", 1)[1])


def test_the_trading_unit_sets_no_success_exit_status_at_all():
    """The strongest form: with none set, ONLY exit 0 is success, so every
    non-zero code inherits the alert without needing to be enumerated."""
    text = (DEPLOY / "bujji-options-os-trading.service").read_text()
    live = [l for l in text.splitlines()
            if l.strip().startswith("SuccessExitStatus")]
    assert live == [], f"the trading unit narrows what counts as failure: {live}"
    assert DEPLOYED_SUCCESS_EXIT_STATUS == "", (
        "the DEPLOYED unit also sets none -- recorded from systemctl cat")


def test_the_alert_unit_exists_and_records_the_failure():
    alert = (DEPLOY / "bujji-alert@.service").read_text()
    assert "ALERTS.jsonl" in alert, (
        "the OnFailure target must leave a durable record, not just a log line")


def test_the_onfailure_wiring_is_deployed_only_and_recorded_here():
    """A FINDING, pinned so it cannot be forgotten: `OnFailure=` lives in a
    drop-in on the host and is NOT version-controlled. The repo therefore
    cannot prove the alert is wired -- only that nothing suppresses it. If the
    drop-in is ever added to deploy/, this test should be replaced by one that
    asserts the file's real content."""
    assert not list(DEPLOY.glob("*.conf")), (
        "a drop-in now exists in deploy/ -- assert its content directly "
        "instead of relying on the recorded fixture")
    assert "OnFailure=bujji-alert@%n.service" in DEPLOYED_DROPINS["onfailure.conf"]


# --------------------------------------------------------------------------
# Behaviour: a pending session cannot exit green.
# --------------------------------------------------------------------------

_CLEAN_EVIDENCE = {
    "prior_tick_journals": {"inspected": True, "unsealed": [], "corrupt": []},
    "tick_journal": {"sealed": True, "faithful": True,
                     "verified": {"outcome": "VERIFIED", "state": "FAITHFUL"},
                     "replay": {"reproduced": True}},
}


def _verdict(summary):
    from bujji.production_runtime.session_safety_verdict import evaluate_session_safety
    return evaluate_session_safety(summary)


def test_a_session_missing_its_evidence_is_not_certified():
    v = _verdict({"entry_filled": False})
    assert v.safe, "nothing is known to be WRONG"
    assert v.pending_evidence
    assert not v.certified, "and it must not be reported as a success"


def test_only_a_fully_evidenced_session_is_certified():
    summary = {"entry_filled": False}
    summary.update(_CLEAN_EVIDENCE)
    assert _verdict(summary).certified


@pytest.mark.parametrize("drop", ["prior_tick_journals", "tick_journal"])
def test_removing_any_evidence_source_removes_certification(drop):
    """NEGATIVE CONTROL on the evidence itself: certification must depend on
    every source, not merely on one of them being present."""
    summary = {"entry_filled": False}
    summary.update(_CLEAN_EVIDENCE)
    summary.pop(drop)
    assert not _verdict(summary).certified


def test_certified_is_not_merely_an_alias_for_safe():
    """If `certified` ever collapses into `safe`, PENDING_EVIDENCE silently
    stops existing and every unprovable session exits 0 again."""
    v = _verdict({"entry_filled": False})
    assert v.safe is True and v.certified is False, (
        "these must be able to disagree -- that difference IS the feature")
