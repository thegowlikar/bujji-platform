"""The trading unit's safety configuration lives in the repository.

Two production safety behaviours -- the operator alert on a failed session,
and the token pre-flight gate -- existed ONLY under
/etc/systemd/system/bujji-options-os-trading.service.d/. They were not
version-controlled, not reviewable in a diff, and not recoverable if the host
were rebuilt. A safety control nobody can review is a safety control nobody
can trust.

These tests assert the DESIRED configuration this repository declares. They do
not read /etc and they do not deploy: installing is a deliberate operator
action, documented in each drop-in's header.
"""
from __future__ import annotations

import pathlib
import re

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
DEPLOY = REPO_ROOT / "deploy"
DROPINS = DEPLOY / "bujji-options-os-trading.service.d"
BASE_UNIT = DEPLOY / "bujji-options-os-trading.service"


def _directives(path: pathlib.Path):
    """Every KEY=VALUE in a unit file, comments and blanks stripped.

    Deliberately not configparser: systemd permits repeated sections and
    repeated keys, and a parser that silently collapses them would hide
    exactly the kind of duplicate this test exists to notice.
    """
    out = []
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("["):
            continue
        if "=" in stripped:
            key, _, value = stripped.partition("=")
            out.append((key.strip(), value.strip()))
    return out


def _values(path, key):
    return [v for k, v in _directives(path) if k == key]


def _runner_exit_codes():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "runner_codes", REPO_ROOT / "bujji_options_os_runner.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return {name: getattr(mod, name) for name in dir(mod) if name.startswith("EXIT_")}


# --------------------------------------------------------------------------
# The drop-ins exist in the repository at all.
# --------------------------------------------------------------------------

def test_the_dropin_directory_is_version_controlled():
    assert DROPINS.is_dir(), (
        "the trading unit's drop-ins must be declared here, not only on the host")


@pytest.mark.parametrize("name", ["onfailure.conf", "token-gate.conf"])
def test_each_dropin_is_present(name):
    assert (DROPINS / name).is_file()


def test_the_dropins_are_documented():
    readme = (DROPINS / "README.md").read_text()
    assert "onfailure.conf" in readme and "token-gate.conf" in readme
    assert "not a deployment" in readme.lower() or "deliberate operator action" in readme


# --------------------------------------------------------------------------
# The token precondition.
# --------------------------------------------------------------------------

def test_the_trading_unit_is_gated_by_the_token_preflight():
    """A session must not start against a token that will die mid-day. On
    2026-08-20 the pre-flight answered FAIL, named this unit, and every timer
    fired anyway -- a detector with no actuator."""
    gate = DROPINS / "token-gate.conf"
    assert "bujji-token-preflight.service" in _values(gate, "Requires"), (
        "Requires= is what makes systemd REFUSE to start this unit on a FAIL "
        "verdict; After= alone would only order them")
    assert "bujji-token-preflight.service" in _values(gate, "After"), (
        "After= is what makes the verdict FRESH rather than a stale 08:45 "
        "opinion")


def test_the_preflight_unit_the_gate_depends_on_exists():
    assert (DEPLOY / "bujji-token-preflight.service").is_file(), (
        "the gate names a unit this repository does not declare")


def test_the_gate_is_not_applied_to_the_backup_unit():
    """A backup must still run when the token is dead -- that is exactly when
    it matters most."""
    backup = DEPLOY / "bujji-backup.service"
    assert "bujji-token-preflight.service" not in _values(backup, "Requires")


# --------------------------------------------------------------------------
# Every non-zero exit reaches the alert.
# --------------------------------------------------------------------------

def test_the_alert_is_declared_for_the_trading_unit():
    assert "bujji-alert@%n.service" in _values(DROPINS / "onfailure.conf", "OnFailure")


def test_the_alert_unit_exists_and_leaves_a_durable_record():
    alert = (DEPLOY / "bujji-alert@.service").read_text()
    assert "ALERTS.jsonl" in alert, (
        "a log line scrolls away; the alert must leave a record an operator "
        "can find afterwards")


def test_the_base_unit_declares_no_success_exit_status():
    """THE mechanism. With none set, systemd treats ONLY exit 0 as success, so
    every non-zero code inherits OnFailure= without being enumerated."""
    assert _values(BASE_UNIT, "SuccessExitStatus") == []


@pytest.mark.parametrize("code_name", sorted(
    n for n, v in _runner_exit_codes().items() if v != 0))
def test_every_non_zero_exit_code_reaches_the_alert(code_name):
    """Parametrised over the runner's OWN codes, so a new outcome added later
    is covered the moment it exists rather than when someone remembers."""
    code = _runner_exit_codes()[code_name]
    declared_success = []
    for unit in DEPLOY.glob("*.service"):
        for value in _values(unit, "SuccessExitStatus"):
            if str(code) in re.findall(r"\d+", value):
                declared_success.append(unit.name)
    for conf in DROPINS.glob("*.conf"):
        for value in _values(conf, "SuccessExitStatus"):
            if str(code) in re.findall(r"\d+", value):
                declared_success.append(conf.name)
    assert declared_success == [], (
        f"{code_name}={code} is declared a success in {declared_success}, so a "
        f"session ending that way would exit green and fire no alert")


def test_the_two_verdict_codes_are_covered_by_name():
    """3 and 4 are the codes the safety verdict itself produces. Named
    explicitly so a reader can see they are handled, not merely swept up by
    the parametrised test above."""
    codes = _runner_exit_codes()
    assert codes["EXIT_UNSAFE_SESSION"] == 3
    assert codes["EXIT_PENDING_EVIDENCE"] == 4
    assert _values(BASE_UNIT, "SuccessExitStatus") == [], (
        "with none declared, both reach OnFailure=")


# --------------------------------------------------------------------------
# The declaration must not drift from what it configures.
# --------------------------------------------------------------------------

def test_the_dropins_configure_the_unit_they_are_named_for():
    """A systemd drop-in only applies to `<unit>.d/`. A directory named for a
    unit that does not exist would be inert and silently so."""
    assert BASE_UNIT.is_file()
    assert DROPINS.name == BASE_UNIT.name + ".d"


def test_no_dropin_reintroduces_a_restart_policy():
    """The base unit deliberately has no Restart=: a oneshot runs once, and a
    mid-session crash must not silently re-enter the market. A drop-in could
    undo that invisibly."""
    for conf in DROPINS.glob("*.conf"):
        assert _values(conf, "Restart") == [], (
            f"{conf.name} adds a restart policy the base unit deliberately omits")
