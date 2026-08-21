"""Every scheduled unit must read the SAME token file: /opt/bujji/.env.

This already went wrong once. On 2026-08-17 a token refresh landed in
`/tmp/local_fyers.env` (modified 20:38) while `/opt/bujji/.env` still held
the morning's token (modified 09:00:48). The two files held DIFFERENT
tokens and nothing consumed the newer one -- the operator believed the
system was refreshed when no unit could see it.

`/tmp` can never work here, for two independent reasons: every unit sets
`PrivateTmp=true` (so each gets its own empty /tmp, by construction not by
accident), and /tmp does not survive a reboot. The trap is easy to fall
into because `/tmp/local_fyers.env` is the argparse DEFAULT of
`run_phase20_13_live_entrypoint.py --fyers-env-file`; the installed unit
overrides it, and this pins that override in place.

See docs/FYERS_TOKEN_LIFECYCLE.md.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

DEPLOY = Path("/opt/bujji/app/deploy")
CANONICAL = "/opt/bujji/.env"

SCHEDULED_UNITS = (
    "bujji-daily-intelligence.service",
    "bujji-shadow-decision-campaign.service",
    "bujji-options-os-trading.service",
)

pytestmark = pytest.mark.skipif(not DEPLOY.is_dir(), reason="deploy/ is VPS-only")


def _directives(name: str) -> list:
    """Directive lines only -- these files discuss /tmp in their comments."""
    return [l.strip() for l in (DEPLOY / name).read_text().splitlines()
            if l.strip() and not l.strip().startswith("#")]


@pytest.mark.parametrize("unit", SCHEDULED_UNITS)
def test_the_unit_names_the_canonical_env_file(unit):
    """Either via EnvironmentFile= or an explicit --fyers-env-file."""
    directives = _directives(unit)
    env_lines = [d for d in directives if d.startswith("EnvironmentFile=")]
    exec_lines = [d for d in directives if d.startswith("ExecStart=")]
    named = any(d == f"EnvironmentFile={CANONICAL}" for d in env_lines) or any(
        f"--fyers-env-file {CANONICAL}" in d for d in exec_lines)
    assert named, f"{unit} does not read {CANONICAL}: env={env_lines}"


@pytest.mark.parametrize("unit", SCHEDULED_UNITS)
def test_no_unit_reads_a_token_from_tmp(unit):
    """PrivateTmp=true makes a host /tmp path unreachable, so a unit
    pointing there would fail in a way that looks like a bad token."""
    for directive in _directives(unit):
        if directive.startswith(("EnvironmentFile=", "ExecStart=")):
            assert "/tmp/" not in directive, f"{unit} reads from /tmp: {directive}"


@pytest.mark.parametrize("unit", SCHEDULED_UNITS)
def test_private_tmp_is_on(unit):
    """The reason /tmp cannot work. If this were ever turned off, a /tmp
    token path would start working intermittently -- worse than failing."""
    assert "PrivateTmp=true" in _directives(unit)


def test_the_campaign_unit_overrides_its_tmp_default():
    """`--fyers-env-file` defaults to /tmp/local_fyers.env in argparse.
    The unit MUST override it; without the override the campaign silently
    reads a file no other unit writes."""
    entry = Path("/opt/bujji/app/scripts/run_phase20_13_live_entrypoint.py")
    if entry.exists():
        default = re.search(r'--fyers-env-file", default="([^"]+)"', entry.read_text())
        assert default and default.group(1).startswith("/tmp/"), (
            "the /tmp default changed -- update this test and the doc, the trap may be gone")
    exec_line = next(d for d in _directives("bujji-shadow-decision-campaign.service")
                     if d.startswith("ExecStart="))
    assert f"--fyers-env-file {CANONICAL}" in exec_line


def test_all_scheduled_units_agree_on_one_file():
    """Two units reading different token files means a refresh can satisfy
    one and starve the other -- the failure is partial, so it looks like a
    bug in whichever unit happened to lose."""
    seen = set()
    for unit in SCHEDULED_UNITS:
        for directive in _directives(unit):
            if directive.startswith("EnvironmentFile="):
                seen.add(directive.split("=", 1)[1])
            elif directive.startswith("ExecStart=") and "--fyers-env-file" in directive:
                seen.add(directive.split("--fyers-env-file", 1)[1].split()[0])
    assert seen == {CANONICAL}, f"units disagree on the token file: {sorted(seen)}"
