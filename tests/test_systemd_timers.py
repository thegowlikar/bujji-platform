"""Fire times are asked for, not asserted.

promote_fyers_token.py carried FIRST_FIRE/LAST_FIRE constants describing the
morning timer bracket. On 2026-08-19 the trading timer moved 09:22:30 -> 09:14
and the constant went stale within a day: it named a unit that had moved, and
pointed EARLIER than the real last token consumer (09:27:30), so the check
reported coverage it was not testing. Same failure class as this repo's four
disagreeing market-close constants.

Both the schedule AND which units consume the token are now discovered from
systemd. These tests pin the parsing, the consumer detection, and -- most
importantly -- that everything degrades to a flagged fallback rather than to
a fabricated bracket when systemd is not there.
"""
from __future__ import annotations

import datetime
import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from bujji.ops import systemd_timers as st

IST = st.IST


def _load_promote():
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "promote_fyers_token", REPO_ROOT / "scripts" / "promote_fyers_token.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestParsingSystemdOutput:
    def test_next_elapse_parses_the_usual_format(self, monkeypatch):
        monkeypatch.setattr(st, "_run", lambda *a, **k: "Wed 2026-08-20 09:14:00 IST\n")
        when = st.next_elapse("bujji-x.timer", IST)
        assert when == datetime.datetime(2026, 8, 20, 9, 14, tzinfo=IST)

    def test_next_elapse_parses_the_dayless_format(self, monkeypatch):
        monkeypatch.setattr(st, "_run", lambda *a, **k: "2026-08-20 09:27:30 IST\n")
        assert st.next_elapse("bujji-x.timer", IST).minute == 27

    @pytest.mark.parametrize("raw", ["", "n/a", "infinity", "not a timestamp at all"])
    def test_an_unusable_elapse_is_none_not_a_guess(self, monkeypatch, raw):
        monkeypatch.setattr(st, "_run", lambda *a, **k: raw)
        assert st.next_elapse("bujji-x.timer", IST) is None

    def test_timers_are_filtered_to_bujji_and_sorted(self, monkeypatch):
        listing = ("bujji-b.timer   loaded active waiting\n"
                   "bujji-a.timer   loaded active waiting\n"
                   "logrotate.timer loaded active waiting\n")
        monkeypatch.setattr(st, "_run", lambda *a, **k: listing)
        assert st.bujji_timers() == ["bujji-a.timer", "bujji-b.timer"]

    def test_exclusions_are_honoured(self, monkeypatch):
        listing = "bujji-a.timer loaded active waiting\nbujji-b.timer loaded active waiting\n"
        monkeypatch.setattr(st, "_run", lambda *a, **k: listing)
        assert st.bujji_timers(exclude=("bujji-a.timer",)) == ["bujji-b.timer"]


class TestFindingTheRightService:
    def test_the_default_is_the_same_name_service(self, monkeypatch):
        monkeypatch.setattr(st, "_unit_text", lambda unit: "[Timer]\nOnCalendar=daily\n")
        assert st.service_for("bujji-x.timer") == "bujji-x.service"

    def test_an_explicit_unit_override_is_honoured(self, monkeypatch):
        """systemd allows Unit= to point elsewhere; assuming same-name would
        inspect the wrong service and mis-detect the token consumer."""
        monkeypatch.setattr(st, "_unit_text",
                            lambda unit: "[Timer]\nUnit=something-else.service\n")
        assert st.service_for("bujji-x.timer") == "something-else.service"


class TestDetectingTokenConsumers:
    def test_environmentfile_wiring_is_detected(self, monkeypatch):
        monkeypatch.setattr(st, "_unit_text",
                            lambda unit: "[Service]\nEnvironmentFile=/opt/bujji/.env\n")
        assert st.consumes_env_file("bujji-x.timer") is True

    def test_explicit_flag_wiring_is_detected(self, monkeypatch):
        """The other style actually in use: --fyers-env-file on ExecStart."""
        monkeypatch.setattr(
            st, "_unit_text",
            lambda unit: "[Service]\nExecStart=/x/py run.py --fyers-env-file /opt/bujji/.env\n")
        assert st.consumes_env_file("bujji-x.timer") is True

    def test_a_unit_that_never_reads_the_token_is_excluded(self, monkeypatch):
        """The 16:30 backup is why this matters: counting it would make a
        token valid to 09:30 report as insufficient and cry wolf daily."""
        monkeypatch.setattr(st, "_unit_text",
                            lambda unit: "[Service]\nExecStart=/x/backup.sh\n")
        assert st.consumes_env_file("bujji-x.timer") is False


class TestItDegradesInsteadOfInventing:
    def test_no_systemd_yields_no_timers(self, monkeypatch):
        monkeypatch.setattr(st, "_run", lambda *a, **k: "")
        assert st.bujji_timers() == []

    def test_no_systemd_yields_an_empty_window_not_a_bracket(self, monkeypatch):
        """The caller must decide what to do about the absence rather than be
        handed a fabricated window that looks like a reading."""
        monkeypatch.setattr(st, "_run", lambda *a, **k: "")
        assert st.token_fire_window() == []

    def test_a_subprocess_failure_is_not_an_exception(self, monkeypatch):
        def explode(*a, **k):
            raise OSError("systemctl missing")

        monkeypatch.setattr(st.subprocess, "run", explode)
        assert st.bujji_timers() == [] and st.host_timezone() is not None

    def test_the_timezone_falls_back_to_the_documented_deployment_tz(self, monkeypatch):
        monkeypatch.setattr(st, "_run", lambda *a, **k: "")
        assert st.host_timezone() == IST


class TestPromoteUsesDiscovery:
    def test_it_reports_the_discovered_unit_by_name(self, monkeypatch):
        mod = _load_promote()
        when = datetime.datetime(2026, 8, 20, 9, 27, 30, tzinfo=IST)
        monkeypatch.setattr(
            "bujji.ops.systemd_timers.token_fire_window",
            lambda **kw: [("bujji-paper-intelligence-campaign.timer", when)])
        fire, label, discovered = mod.last_token_fire(datetime.datetime.now(IST))
        assert discovered is True
        assert fire == when and label == "bujji-paper-intelligence-campaign"

    def test_it_takes_the_LAST_fire_not_the_first(self, monkeypatch):
        """The question is whether the token survives every fire, so the
        binding one is the latest."""
        mod = _load_promote()
        early = datetime.datetime(2026, 8, 20, 9, 10, tzinfo=IST)
        late = datetime.datetime(2026, 8, 20, 9, 27, 30, tzinfo=IST)
        monkeypatch.setattr(
            "bujji.ops.systemd_timers.token_fire_window",
            lambda **kw: [("bujji-early.timer", early), ("bujji-late.timer", late)])
        fire, _, _ = mod.last_token_fire(datetime.datetime.now(IST))
        assert fire == late

    def test_it_falls_back_and_says_so_when_systemd_is_absent(self, monkeypatch):
        mod = _load_promote()
        monkeypatch.setattr("bujji.ops.systemd_timers.token_fire_window",
                            lambda **kw: [])
        fire, label, discovered = mod.last_token_fire(datetime.datetime.now(IST))
        assert discovered is False
        assert "fallback" in label.lower()
        assert fire.timetz().replace(tzinfo=None) == mod.FALLBACK_LAST_FIRE

    def test_the_fallback_constant_matches_the_real_last_consumer(self):
        """If the fallback is ever reached it should still be right."""
        mod = _load_promote()
        assert mod.FALLBACK_LAST_FIRE == datetime.time(9, 27, 30)


@pytest.mark.skipif(not Path("/etc/systemd/system/bujji-options-os-trading.timer").exists(),
                    reason="units installed only on the VPS")
class TestAgainstTheRealHost:
    def test_the_discovered_window_is_not_empty(self):
        assert st.token_fire_window(exclude=("bujji-token-preflight.timer",))

    def test_the_backup_timer_is_not_a_token_consumer(self):
        """Positive control for the consumer filter: bujji-backup runs at
        16:30 and would wrongly dominate the window if it were counted."""
        names = [t for t, _ in st.token_fire_window()]
        assert not any("backup" in name for name in names)

    def test_the_trading_timer_is_a_token_consumer(self):
        names = [t for t, _ in st.token_fire_window()]
        assert any("options-os-trading" in name for name in names)
