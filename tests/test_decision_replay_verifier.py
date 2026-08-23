"""The replay verifier must never award a level it did not demonstrate.

The failure mode this guards against is not a crash. It is a verifier that
reports FULL_DECISION_EQUIVALENCE on a package that merely looks tidy --
which would convert "we have not proven replay" into "replay is proven"
without anyone noticing. Every test here is about refusing to over-claim.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "decision_replay_verifier",
    Path(__file__).resolve().parent.parent / "tools" / "decision_replay_verifier.py",
)
drv = importlib.util.module_from_spec(_SPEC)
# REGISTERED BEFORE exec_module, ON PURPOSE. @dataclass resolves a class's
# annotations via sys.modules[cls.__module__], which is None for a module
# loaded by path and never registered -- the decorator then raises
# AttributeError at import time. The same shape cost seven collection errors
# in bujji_options_os_runner.py. The fix is to register the module, not to
# stop using dataclasses in tools.
sys.modules[_SPEC.name] = drv
_SPEC.loader.exec_module(drv)


def _package(tmp_path, decisions, *, session_id="S1", orders=None, metadata=True):
    pkg = tmp_path / session_id
    pkg.mkdir()
    (pkg / "decisions.jsonl").write_text(
        "\n".join(json.dumps(d) for d in decisions) + ("\n" if decisions else ""))
    if metadata:
        (pkg / "metadata.json").write_text(json.dumps({"session_id": session_id}))
    if orders is not None:
        (pkg / "orders.jsonl").write_text(
            "\n".join(json.dumps(o) for o in orders) + "\n")
    return pkg


def _selection(session_id, trend, vol, selected, ts, *, candidates=None, extra=None):
    exp = {"stage": drv.SELECTION_STAGE, "session_id": session_id,
           "trend_regime": trend, "volatility_regime": vol,
           "selected_strategy": selected}
    if candidates is not None:
        exp["candidates"] = candidates
    if extra:
        exp.update(extra)
    return {"timestamp": ts, "decision_owner": "governor",
            "output_decision": drv.SELECTION_STAGE, "explanation": exp,
            "trace_id": "t1"}


class TestItRefusesToAwardWhatItCannotShow:
    def test_an_empty_package_is_level_zero(self, tmp_path):
        pkg = _package(tmp_path, [])
        assert drv.verify(pkg).achieved == 0

    def test_a_missing_decisions_file_is_level_zero(self, tmp_path):
        pkg = tmp_path / "S1"
        pkg.mkdir()
        assert drv.verify(pkg).achieved == 0

    def test_unparseable_records_are_level_zero(self, tmp_path):
        pkg = tmp_path / "S1"
        pkg.mkdir()
        (pkg / "decisions.jsonl").write_text("{not json\n")
        assert drv.verify(pkg).achieved == 0

    def test_eligibility_replay_alone_does_not_earn_level_three(self, tmp_path):
        """THE CENTRAL GUARANTEE, and the state every real package is in
        today. Levels are strictly ordered: proving eligibility replay while
        the analytical inputs were never recorded earns level 1, not 3."""
        pkg = _package(tmp_path, [
            _selection("S1", "SIDEWAYS", "CONTRACTION", "NEUTRAL_PREMIUM_SELLING",
                       "2026-08-20T09:51:27+05:30"),
        ])
        report = drv.verify(pkg)
        level3 = [f for f in report.findings if f.level == 3]
        assert level3 and all(f.ok for f in level3), "level 3 checks should have passed"
        assert 2 in report.blocked_because
        assert report.achieved == 1, (
            "eligibility replay was demonstrated but level 2 was never attempted; "
            "awarding 3 would claim a chain that was not verified end to end")

    def test_a_session_with_no_orders_can_never_exceed_level_three(self, tmp_path):
        pkg = _package(tmp_path, [
            _selection("S1", "SIDEWAYS", "CONTRACTION", "NEUTRAL_PREMIUM_SELLING",
                       "2026-08-20T09:51:27+05:30",
                       extra={"market_snapshot_ref": "snap-1"}),
        ])
        report = drv.verify(pkg)
        assert 4 in report.blocked_because
        assert report.achieved <= 3

    def test_recorded_orders_still_do_not_earn_level_four(self, tmp_path):
        """Orders existing is not the same as strikes being re-derivable. The
        chain they were chosen from is not in the package."""
        pkg = _package(
            tmp_path,
            [_selection("S1", "SIDEWAYS", "CONTRACTION", "NEUTRAL_PREMIUM_SELLING",
                        "2026-08-20T09:51:27+05:30",
                        extra={"market_snapshot_ref": "snap-1"})],
            orders=[{"stage": "ORDER_SUBMITTED", "symbol": "NIFTY-CE"}])
        report = drv.verify(pkg)
        assert report.achieved < 4
        assert "chain snapshot" in report.blocked_because[4]


class TestItDetectsRealDisagreement:
    def test_a_selection_that_does_not_recompute_fails_level_three(self, tmp_path):
        """The recorded regime cannot produce this family in either risk mode."""
        pkg = _package(tmp_path, [
            _selection("S1", "SIDEWAYS", "CONTRACTION", "BEAR_CALL_SPREAD",
                       "2026-08-20T09:51:27+05:30",
                       extra={"market_snapshot_ref": "snap-1"}),
        ])
        report = drv.verify(pkg)
        failures = [f for f in report.findings if f.level == 3 and not f.ok]
        assert failures, "a fabricated selection was accepted as reproducible"
        assert report.achieved < 3

    def test_a_tampered_candidate_set_fails_level_three(self, tmp_path):
        from bujji.production_runtime.trading_session_governor import strategy_selector as ss
        real = [{"family": c.family, "status": c.status, "reason_code": c.reason_code,
                 "detail": c.detail}
                for c in ss._evaluate_candidates("SIDEWAYS", "CONTRACTION", False)]
        tampered = [dict(c) for c in real]
        tampered[0]["reason_code"] = "SOMETHING_ELSE"
        pkg = _package(tmp_path, [
            _selection("S1", "SIDEWAYS", "CONTRACTION", "NEUTRAL_PREMIUM_SELLING",
                       "2026-08-20T09:51:27+05:30", candidates=tampered,
                       extra={"market_snapshot_ref": "snap-1"}),
        ])
        report = drv.verify(pkg)
        assert any(f.level == 3 and not f.ok for f in report.findings)

    def test_an_untampered_candidate_set_reproduces_exactly(self, tmp_path):
        from bujji.production_runtime.trading_session_governor import strategy_selector as ss
        real = [{"family": c.family, "status": c.status, "reason_code": c.reason_code,
                 "detail": c.detail}
                for c in ss._evaluate_candidates("SIDEWAYS", "CONTRACTION", False)]
        pkg = _package(tmp_path, [
            _selection("S1", "SIDEWAYS", "CONTRACTION", "NEUTRAL_PREMIUM_SELLING",
                       "2026-08-20T09:51:27+05:30", candidates=real,
                       extra={"market_snapshot_ref": "snap-1"}),
        ])
        report = drv.verify(pkg)
        assert all(f.ok for f in report.findings if f.level == 3)
        assert report.achieved == 3

    def test_records_from_two_sessions_fail_integrity(self, tmp_path):
        pkg = _package(tmp_path, [
            _selection("S1", "SIDEWAYS", "CONTRACTION", None, "2026-08-20T09:00:00+05:30"),
            _selection("S2", "SIDEWAYS", "CONTRACTION", None, "2026-08-20T09:01:00+05:30"),
        ])
        report = drv.verify(pkg)
        assert any(f.level == 1 and not f.ok for f in report.findings)
        assert report.achieved == 0

    def test_out_of_order_timestamps_fail_integrity(self, tmp_path):
        pkg = _package(tmp_path, [
            _selection("S1", "SIDEWAYS", "CONTRACTION", None, "2026-08-20T10:00:00+05:30"),
            _selection("S1", "SIDEWAYS", "CONTRACTION", None, "2026-08-20T09:00:00+05:30"),
        ])
        report = drv.verify(pkg)
        assert any(f.level == 1 and not f.ok for f in report.findings)
        assert report.achieved == 0


class TestItIsReadOnly:
    def test_verifying_changes_nothing_on_disk(self, tmp_path):
        pkg = _package(tmp_path, [
            _selection("S1", "SIDEWAYS", "CONTRACTION", "NEUTRAL_PREMIUM_SELLING",
                       "2026-08-20T09:51:27+05:30"),
        ])
        before = {p.name: (p.stat().st_mtime_ns, p.read_bytes())
                  for p in sorted(pkg.iterdir())}
        drv.verify(pkg)
        after = {p.name: (p.stat().st_mtime_ns, p.read_bytes())
                 for p in sorted(pkg.iterdir())}
        assert before == after, "the verifier modified the evidence it was reading"


class TestTheOperatorResult:
    def test_require_level_is_what_makes_it_a_gate(self, tmp_path, monkeypatch, capsys):
        pkg = _package(tmp_path, [
            _selection("S1", "SIDEWAYS", "CONTRACTION", "NEUTRAL_PREMIUM_SELLING",
                       "2026-08-20T09:51:27+05:30"),
        ])
        monkeypatch.setattr("sys.argv",
                            ["drv", str(pkg), "--require-level", "3"])
        assert drv.main() == drv.EXIT_PENDING_EVIDENCE
        assert "PENDING_EVIDENCE" in capsys.readouterr().out

    def test_a_missing_package_is_a_config_error(self, tmp_path, monkeypatch):
        monkeypatch.setattr("sys.argv", ["drv", str(tmp_path / "nope")])
        assert drv.main() == drv.EXIT_CONFIG_ERROR

    def test_the_report_always_says_why_it_stopped(self, tmp_path):
        pkg = _package(tmp_path, [
            _selection("S1", "SIDEWAYS", "CONTRACTION", "NEUTRAL_PREMIUM_SELLING",
                       "2026-08-20T09:51:27+05:30"),
        ])
        text = drv.render(drv.verify(pkg))
        assert "STRONGEST HONEST REPLAY LEVEL" in text
        assert "WHY NOT" in text, (
            "a report that names a level without naming the obstacle to the next one "
            "reads as a ceiling rather than a gap")
