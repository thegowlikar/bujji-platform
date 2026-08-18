"""Tests for the FYERS token pre-flight verdict.

`assess()` is deliberately pure (no systemd, no filesystem, no clock) so the
decision itself is testable without a live host. Assertions are positive and
structural -- they check the verdict object, never a substring of prose.
"""
from __future__ import annotations

import datetime
import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

_spec = importlib.util.spec_from_file_location(
    "preflight_fyers_token", REPO_ROOT / "scripts" / "preflight_fyers_token.py")
preflight = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(preflight)

IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))


def _dt(h, m, s=0, day=19):
    return datetime.datetime(2026, 8, day, h, m, s, tzinfo=IST)


NOW = _dt(8, 45)
FIRES = [
    ("bujji-shadow-decision-campaign.timer", _dt(9, 10)),
    ("bujji-daily-intelligence.timer", _dt(9, 16)),
    ("bujji-options-os-trading.timer", _dt(9, 22, 30)),
]


def test_token_outliving_every_fire_is_ok():
    v = preflight.assess(_dt(15, 40), FIRES, NOW)
    assert v["status"] == "OK"
    assert [c["covered"] for c in v["fires"]] == [True, True, True]


def test_token_expiring_before_all_fires_fails_and_names_every_uncovered_timer():
    # The real 2026-08-19 case: token dies at the 06:00 cutover.
    v = preflight.assess(_dt(6, 0), FIRES, NOW)
    assert v["status"] == "FAIL"
    assert [c["covered"] for c in v["fires"]] == [False, False, False]
    assert all(t in v["reason"] for t, _ in FIRES)


def test_partial_coverage_fails_and_names_only_the_uncovered():
    # Dies at 09:15 -- covers the 09:10 campaign, misses capture and trading.
    v = preflight.assess(_dt(9, 15), FIRES, NOW)
    assert v["status"] == "FAIL"
    assert [c["covered"] for c in v["fires"]] == [True, False, False]
    assert "bujji-shadow-decision-campaign.timer" not in v["reason"]
    assert "bujji-daily-intelligence.timer" in v["reason"]
    assert "bujji-options-os-trading.timer" in v["reason"]


def test_expiry_exactly_at_fire_time_is_not_covered():
    # A token expiring at the instant of the fire must not count as valid:
    # the request goes out at or after that instant.
    v = preflight.assess(_dt(9, 16), [FIRES[1]], NOW)
    assert v["status"] == "FAIL"
    assert v["fires"][0]["covered"] is False


def test_undecodable_token_fails_rather_than_passing_silently():
    v = preflight.assess(None, FIRES, NOW)
    assert v["status"] == "FAIL"
    assert v["token_expires_at"] is None
    assert all(c["covered"] is False for c in v["fires"])


def test_no_upcoming_fires_is_ok_not_a_false_alarm():
    v = preflight.assess(_dt(6, 0), [], NOW)
    assert v["status"] == "OK"
    assert v["fires"] == []


def test_verdict_records_every_timer_it_checked():
    v = preflight.assess(_dt(15, 40), FIRES, NOW)
    assert [c["timer"] for c in v["fires"]] == [t for t, _ in FIRES]
    assert [c["fires_at"] for c in v["fires"]] == [f.isoformat() for _, f in FIRES]
    assert v["checked_at"] == NOW.isoformat()


def test_verdict_is_json_serialisable():
    import json
    json.loads(json.dumps(preflight.assess(_dt(15, 40), FIRES, NOW)))


@pytest.mark.parametrize("raw,expected", [
    ("Wed 2026-08-19 09:16:00 IST", _dt(9, 16)),
    ("Wed 2026-08-19 09:22:30 IST", _dt(9, 22, 30)),
])
def test_systemd_timestamp_parsing(monkeypatch, raw, expected):
    """The exact string format systemd 255 emits for NextElapseUSecRealtime."""
    class _R:
        stdout = raw
    monkeypatch.setattr(preflight.subprocess, "run", lambda *a, **k: _R())
    assert preflight.next_elapse("bujji-x.timer", IST) == expected


@pytest.mark.parametrize("raw", ["", "n/a", "infinity"])
def test_unscheduled_timer_yields_no_fire(monkeypatch, raw):
    class _R:
        stdout = raw
    monkeypatch.setattr(preflight.subprocess, "run", lambda *a, **k: _R())
    assert preflight.next_elapse("bujji-x.timer", IST) is None


def test_own_timer_is_excluded_from_token_consumers(monkeypatch):
    """The pre-flight reads a local JWT and never calls the broker, so its own
    timer must not be counted as something the token has to outlive."""
    listing = (
        "bujji-token-preflight.timer          loaded active waiting Pre-open trigger\n"
        "bujji-daily-intelligence.timer       loaded active waiting Daily trigger\n"
        "bujji-options-os-trading.timer       loaded active waiting Daily trigger\n"
    )

    class _R:
        stdout = listing

    monkeypatch.setattr(preflight.subprocess, "run", lambda *a, **k: _R())
    found = preflight.active_bujji_timers()
    assert preflight.SELF_TIMER not in found
    assert found == [
        "bujji-daily-intelligence.timer",
        "bujji-options-os-trading.timer",
    ]
