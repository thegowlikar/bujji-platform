"""The readiness gate must refuse, and must never launder UNKNOWN into PASS.

A readiness checker that always says READY is worse than none: it converts an
unexamined system into an endorsed one. These tests are about the refusals.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "paper_session_readiness",
    Path(__file__).resolve().parent.parent / "tools" / "paper_session_readiness.py",
)
psr = importlib.util.module_from_spec(_SPEC)
# See tests/test_decision_replay_verifier.py -- @dataclass needs the module
# registered before exec_module or it raises on a path-loaded module.
sys.modules[_SPEC.name] = psr
_SPEC.loader.exec_module(psr)


class TestUnknownIsNeverPass:
    def test_a_single_unknown_blocks_ready(self):
        r = psr.Readiness()
        r.add("IDENTITY", "a", psr.PASS, "fine")
        r.add("EVIDENCE", "b", psr.UNKNOWN, "could not tell")
        assert r.verdict == "PENDING_EVIDENCE", (
            "an undetermined check was treated as a satisfied one")

    def test_a_single_fail_refuses_even_beside_passes(self):
        r = psr.Readiness()
        for i in range(5):
            r.add("IDENTITY", f"ok{i}", psr.PASS, "fine")
        r.add("CONFIGURATION", "bad", psr.FAIL, "wrong")
        assert r.verdict == "REFUSED"

    def test_fail_outranks_unknown(self):
        r = psr.Readiness()
        r.add("A", "u", psr.UNKNOWN, "")
        r.add("B", "f", psr.FAIL, "")
        assert r.verdict == "REFUSED"

    def test_all_pass_is_the_only_route_to_ready(self):
        r = psr.Readiness()
        r.add("A", "x", psr.PASS, "")
        assert r.verdict == "READY"

    def test_an_empty_assessment_is_not_ready(self):
        """Nothing checked is not the same as everything fine. A misconfigured
        invocation that runs no checks must not print READY -- an unexamined
        system would come back endorsed."""
        r = psr.Readiness()
        assert r.verdict == "PENDING_EVIDENCE"


class TestConfigurationRefusals:
    def test_shadow_mode_absent_is_a_refusal(self, tmp_path):
        cfg = tmp_path / "c.yaml"
        cfg.write_text("session:\n  continuous: true\n")
        r = psr.Readiness()
        psr.check_configuration(r, cfg)
        statuses = {c.name: c.status for c in r.checks}
        assert statuses["execution is shadow mode"] == psr.FAIL

    def test_shadow_mode_false_is_a_refusal(self, tmp_path):
        cfg = tmp_path / "c.yaml"
        cfg.write_text("shadow_mode: false\n")
        r = psr.Readiness()
        psr.check_configuration(r, cfg)
        assert any(c.status == psr.FAIL for c in r.checks)

    def test_a_truthy_non_literal_true_is_still_a_refusal(self, tmp_path):
        """The runner derives defined-risk-only from a LITERAL true. A string
        'true' is not that, and must not read as safe here either."""
        cfg = tmp_path / "c.yaml"
        cfg.write_text('shadow_mode: "true"\n')
        r = psr.Readiness()
        psr.check_configuration(r, cfg)
        verdicts = {c.name: c.status for c in r.checks}
        assert verdicts["execution is shadow mode"] == psr.FAIL

    def test_literal_true_passes(self, tmp_path):
        cfg = tmp_path / "c.yaml"
        cfg.write_text("shadow_mode: true\n")
        r = psr.Readiness()
        psr.check_configuration(r, cfg)
        assert all(c.status == psr.PASS for c in r.checks)

    def test_an_unreadable_config_is_unknown_not_fail(self, tmp_path):
        """It might be fine; we cannot tell. UNKNOWN blocks READY anyway, so
        nothing is lost by being accurate about which it is."""
        r = psr.Readiness()
        psr.check_configuration(r, tmp_path / "missing.yaml")
        assert all(c.status == psr.UNKNOWN for c in r.checks)


class TestIdentityRefusals:
    @staticmethod
    def _repo(tmp_path, name, dirty=False):
        repo = tmp_path / name
        repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
        (repo / "f.txt").write_text("a")
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-qm", "x"], cwd=repo, check=True)
        if dirty:
            (repo / "f.txt").write_text("b")
        return repo

    def test_a_dirty_deployed_checkout_is_refused(self, tmp_path):
        live = self._repo(tmp_path, "live", dirty=True)
        branch = self._repo(tmp_path, "branch")
        r = psr.Readiness()
        psr.check_identity(r, live, branch, None)
        failed = [c for c in r.checks if c.status == psr.FAIL]
        assert any("deployed checkout is clean" in c.name for c in failed)

    def test_a_dirty_branch_is_refused(self, tmp_path):
        live = self._repo(tmp_path, "live")
        branch = self._repo(tmp_path, "branch", dirty=True)
        r = psr.Readiness()
        psr.check_identity(r, live, branch, None)
        assert any("verified branch is clean" in c.name
                   for c in r.checks if c.status == psr.FAIL)

    def test_a_wrong_commit_is_refused(self, tmp_path):
        live = self._repo(tmp_path, "live")
        branch = self._repo(tmp_path, "branch")
        r = psr.Readiness()
        psr.check_identity(r, live, branch, "deadbee")
        assert any("expected commit" in c.name
                   for c in r.checks if c.status == psr.FAIL)

    def test_omitting_the_expected_commit_is_unknown_not_pass(self, tmp_path):
        """The check that would otherwise be silently skipped. Not supplying
        an expectation means nothing verified the identity -- which is not the
        same as the identity being right."""
        live = self._repo(tmp_path, "live")
        branch = self._repo(tmp_path, "branch")
        r = psr.Readiness()
        psr.check_identity(r, live, branch, None)
        expectation = [c for c in r.checks if "expected commit" in c.name]
        assert expectation and expectation[0].status == psr.UNKNOWN
        assert r.verdict != "READY"

    def test_a_matching_commit_passes(self, tmp_path):
        live = self._repo(tmp_path, "live")
        branch = self._repo(tmp_path, "branch")
        sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=live,
                             capture_output=True, text=True).stdout.strip()[:7]
        r = psr.Readiness()
        psr.check_identity(r, live, branch, sha)
        assert all(c.status == psr.PASS for c in r.checks)


class TestEvidenceRefusals:
    def test_a_missing_sessions_root_is_unknown(self, tmp_path):
        r = psr.Readiness()
        psr.check_evidence(r, tmp_path / "nope", 2, Path("tools"))
        assert all(c.status == psr.UNKNOWN for c in r.checks)
        assert r.verdict != "READY"

    def test_an_empty_sessions_root_is_unknown(self, tmp_path):
        root = tmp_path / "sessions"
        root.mkdir()
        r = psr.Readiness()
        psr.check_evidence(r, root, 2, Path("tools"))
        assert all(c.status == psr.UNKNOWN for c in r.checks)

    def test_a_package_below_the_required_level_is_refused(self, tmp_path):
        """End to end against the real verifier: a package with no analytical
        binding cannot reach level 2, so readiness must refuse."""
        root = tmp_path / "sessions"
        root.mkdir()
        pkg = root / "S1"
        pkg.mkdir()
        (pkg / "metadata.json").write_text(json.dumps({"session_id": "S1"}))
        (pkg / "decisions.jsonl").write_text(json.dumps({
            "timestamp": "2026-08-20T09:00:00+05:30",
            "explanation": {"stage": "STRATEGY_SELECTION_EVALUATED",
                            "session_id": "S1", "trend_regime": "SIDEWAYS",
                            "volatility_regime": "CONTRACTION",
                            "selected_strategy": "NEUTRAL_PREMIUM_SELLING"}}) + "\n")
        r = psr.Readiness()
        psr.check_evidence(r, root, 2, Path("tools").resolve())
        assert any(c.status == psr.FAIL for c in r.checks), (
            "a package that cannot replay was accepted as sufficient evidence")


class TestItIsReadOnly:
    def test_no_check_mutates_the_paths_it_inspects(self, tmp_path):
        cfg = tmp_path / "c.yaml"
        cfg.write_text("shadow_mode: true\n")
        root = tmp_path / "sessions"
        root.mkdir()
        before = sorted((p, p.stat().st_mtime_ns) for p in tmp_path.rglob("*"))
        r = psr.Readiness()
        psr.check_configuration(r, cfg)
        psr.check_evidence(r, root, 2, Path("tools").resolve())
        after = sorted((p, p.stat().st_mtime_ns) for p in tmp_path.rglob("*"))
        assert before == after

    def test_the_module_names_no_mutating_systemctl_verb(self):
        """A readiness tool must not be able to change what it is judging."""
        src = (Path(__file__).resolve().parent.parent
               / "tools" / "paper_session_readiness.py").read_text()
        for verb in ('"enable"', '"disable"', '"start"', '"stop"',
                     '"mask"', '"unmask"', '"daemon-reload"'):
            assert verb not in src, f"the readiness tool references systemctl {verb}"
