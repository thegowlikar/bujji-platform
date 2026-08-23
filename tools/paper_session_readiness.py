#!/usr/bin/env python3
"""Refuse a paper session that the evidence does not support.

READ-ONLY, AND DELIBERATELY POWERLESS. This tool prints a verdict. It cannot
deploy, enable, install, start, stop, place, cancel or modify anything, and it
holds no credential. Its only output is a decision an operator then acts on.

WHAT "READY" MEANS HERE. Not "the tests pass" -- they pass on a branch nobody
has deployed. Ready means the four things a paper session's evidence depends
on actually agree with each other:

  1. IDENTITY      the code that would run is the code that was verified
  2. INHIBITION    nothing can start an order-capable session by accident
  3. CONFIGURATION the declared execution mode is the safe one, and the risk
                   mode derived from it is the defended one
  4. EVIDENCE      the last session produced a package that replays

Every check is PASS, FAIL or UNKNOWN, and UNKNOWN IS NEVER TREATED AS PASS.
That is the whole discipline: a check that could not run has not been
satisfied, and the difference between "no orphan exposure" and "could not
determine whether there is orphan exposure" is the difference between safe
and unsafe. The verdict is READY only when every check is PASS.

Exit codes follow the house convention:
  0  READY
  1  CONFIG_ERROR     -- bad arguments or unreadable paths
  2  RUNTIME_ERROR    -- this tool itself failed
  4  PENDING_EVIDENCE -- a check returned UNKNOWN; nothing is proven either way
  6  REFUSED          -- a check actively FAILED
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

EXIT_READY = 0
EXIT_CONFIG_ERROR = 1
EXIT_RUNTIME_ERROR = 2
EXIT_PENDING_EVIDENCE = 4
EXIT_REFUSED = 6

PASS, FAIL, UNKNOWN = "PASS", "FAIL", "UNKNOWN"


@dataclass
class Check:
    group: str
    name: str
    status: str
    detail: str
    remedy: str = ""


@dataclass
class Readiness:
    checks: List[Check] = field(default_factory=list)

    def add(self, group, name, status, detail, remedy="") -> None:
        self.checks.append(Check(group, name, status, detail, remedy))

    @property
    def failed(self) -> List[Check]:
        return [c for c in self.checks if c.status == FAIL]

    @property
    def unknown(self) -> List[Check]:
        return [c for c in self.checks if c.status == UNKNOWN]

    @property
    def verdict(self) -> str:
        # NOTHING CHECKED IS NOT EVERYTHING FINE. An assessment that ran no
        # checks at all must not report READY: that is the shape a
        # misconfigured invocation takes, and it would turn an unexamined
        # system into an endorsed one. assess() always runs all four groups,
        # so this is a backstop rather than a reachable state today -- which
        # is exactly when a fail-open default goes unnoticed.
        if not self.checks:
            return "PENDING_EVIDENCE"
        if self.failed:
            return "REFUSED"
        if self.unknown:
            return "PENDING_EVIDENCE"
        return "READY"


def _run(args, cwd=None, timeout=60):
    """Run a read-only command. Returns (ok, stdout, err-description)."""
    try:
        p = subprocess.run(args, cwd=cwd, capture_output=True, text=True,
                           timeout=timeout)
    except FileNotFoundError:
        return False, "", f"{args[0]} is not installed"
    except subprocess.TimeoutExpired:
        return False, "", f"{' '.join(args[:3])} timed out"
    except Exception as exc:  # noqa: BLE001
        return False, "", f"{type(exc).__name__}: {exc}"
    if p.returncode != 0:
        return False, p.stdout, (p.stderr or p.stdout).strip()[:200]
    return True, p.stdout, ""


# --------------------------------------------------------------------------
# 1. Identity -- is the code that would run the code that was verified?
# --------------------------------------------------------------------------
def check_identity(r: Readiness, checkout: Path, branch_repo: Path,
                   expect_sha: Optional[str]) -> None:
    ok, out, err = _run(["git", "rev-parse", "HEAD"], cwd=checkout)
    if not ok:
        r.add("IDENTITY", "the deployed checkout has a resolvable commit", UNKNOWN,
              f"could not read {checkout}: {err}",
              "confirm the path is a git checkout before trusting any claim about it")
        return
    live_sha = out.strip()

    ok, out, _ = _run(["git", "status", "--porcelain"], cwd=checkout)
    if not ok:
        r.add("IDENTITY", "the deployed checkout is clean", UNKNOWN,
              "could not determine whether the checkout has uncommitted changes")
    elif out.strip():
        n = len(out.strip().splitlines())
        r.add("IDENTITY", "the deployed checkout is clean", FAIL,
              f"{n} uncommitted change(s) in {checkout}; the running code is not any "
              f"commit, so no test result describes it",
              "commit or revert them, then re-run this check")
    else:
        r.add("IDENTITY", "the deployed checkout is clean", PASS,
              f"{checkout} is at {live_sha[:7]} with no local modifications")

    ok, out, _ = _run(["git", "status", "--porcelain"], cwd=branch_repo)
    if not ok:
        r.add("IDENTITY", "the verified branch is clean", UNKNOWN,
              f"could not read {branch_repo}")
    elif out.strip():
        r.add("IDENTITY", "the verified branch is clean", FAIL,
              f"{len(out.strip().splitlines())} uncommitted change(s) in {branch_repo}; "
              f"the suite result does not describe the working tree",
              "commit the work before treating the test run as evidence")
    else:
        r.add("IDENTITY", "the verified branch is clean", PASS,
              f"{branch_repo} has no local modifications")

    if expect_sha:
        if live_sha.startswith(expect_sha) or expect_sha.startswith(live_sha[:7]):
            r.add("IDENTITY", "the checkout is the expected commit", PASS,
                  f"{live_sha[:7]} matches the expected {expect_sha}")
        else:
            r.add("IDENTITY", "the checkout is the expected commit", FAIL,
                  f"expected {expect_sha}, found {live_sha[:7]}",
                  "the deployed code is not what was verified; do not run")
    else:
        r.add("IDENTITY", "the checkout is the expected commit", UNKNOWN,
              f"no expected commit was supplied; {checkout} is at {live_sha[:7]} and "
              f"nothing here can say whether that is the intended one",
              "pass --expect-sha with the commit the operator intends to run")


# --------------------------------------------------------------------------
# 2. Inhibition -- can anything start an order-capable session?
# --------------------------------------------------------------------------
def check_inhibition(r: Readiness, units: List[str]) -> None:
    for unit in units:
        ok, out, err = _run(["systemctl", "is-enabled", unit])
        state = (out or "").strip() or "unknown"
        if not ok and state not in ("disabled", "masked", "static", "indirect"):
            # is-enabled exits non-zero for disabled units too, so a non-zero
            # exit is only alarming when the word it printed is not a
            # recognised inhibited state.
            if state == "unknown" or not state:
                r.add("INHIBITION", f"{unit} is not enabled", UNKNOWN,
                      f"could not determine the enablement of {unit}: {err or 'no output'}",
                      "resolve before running; an undetermined unit is not a disabled one")
                continue
        if state in ("disabled", "masked"):
            r.add("INHIBITION", f"{unit} is not enabled", PASS,
                  f"{unit} is {state}")
        elif state == "static":
            # A static unit has no [Install] section, so `systemctl enable`
            # cannot succeed and it can never start at boot on its own. It
            # remains startable by its timer or by hand. That is a real
            # inhibition, but only jointly with the timer check below -- which
            # is why the timer is checked as its own unit rather than being
            # assumed.
            r.add("INHIBITION", f"{unit} is not enabled", PASS,
                  f"{unit} is static: it has no [Install] section, so it cannot be "
                  f"enabled or started at boot. It remains reachable from its timer, "
                  f"which is checked separately")
        elif state == "enabled":
            r.add("INHIBITION", f"{unit} is not enabled", FAIL,
                  f"{unit} is ENABLED and may start on its own",
                  f"systemctl disable {unit} (operator decision, not this tool's)")
        else:
            r.add("INHIBITION", f"{unit} is not enabled", UNKNOWN,
                  f"{unit} reports {state!r}, which is neither clearly enabled nor "
                  f"clearly inhibited")


# --------------------------------------------------------------------------
# 3. Configuration -- is the declared mode the safe one?
# --------------------------------------------------------------------------
def check_configuration(r: Readiness, config_path: Path) -> None:
    if not config_path.exists():
        r.add("CONFIGURATION", "the session config is readable", UNKNOWN,
              f"{config_path} does not exist")
        return
    try:
        import yaml
        cfg = yaml.safe_load(config_path.read_text())
    except Exception as exc:  # noqa: BLE001
        r.add("CONFIGURATION", "the session config is readable", UNKNOWN,
              f"{config_path} could not be parsed: {type(exc).__name__}: {exc}")
        return
    if not isinstance(cfg, dict):
        r.add("CONFIGURATION", "the session config is readable", FAIL,
              f"{config_path} did not parse to a mapping")
        return
    r.add("CONFIGURATION", "the session config is readable", PASS,
          f"{config_path.name} parsed")

    # shadow_mode must be a LITERAL true. The runner derives defined-risk-only
    # from exactly this, failing closed: anything other than literal true
    # selects defined-risk. A missing key, a typo and a real-money switch all
    # land safe -- and all three should be visible here rather than inferred.
    shadow = cfg.get("shadow_mode", "<absent>")
    if shadow is True:
        r.add("CONFIGURATION", "execution is shadow mode", PASS,
              "shadow_mode is literally true; execution is neutered by "
              "bujji.broker.guard.disable_live_execution()")
    elif shadow == "<absent>":
        r.add("CONFIGURATION", "execution is shadow mode", FAIL,
              "shadow_mode is absent. The runner fails closed to defined-risk-only, "
              "but an absent execution-mode declaration is not something to run on",
              "declare shadow_mode explicitly")
    else:
        r.add("CONFIGURATION", "execution is shadow mode", FAIL,
              f"shadow_mode is {shadow!r}, not literal true. This is the switch that "
              f"decides whether execution is neutered",
              "do not run until this is understood")


# --------------------------------------------------------------------------
# 4. Evidence -- did the last session leave a package that replays?
# --------------------------------------------------------------------------
def check_evidence(r: Readiness, sessions_root: Path, require_level: int,
                   tools_dir: Path) -> None:
    if not sessions_root.is_dir():
        r.add("EVIDENCE", "a previous session package exists", UNKNOWN,
              f"{sessions_root} is not a directory; there is no prior session to judge")
        return
    packages = sorted((p for p in sessions_root.iterdir() if p.is_dir()),
                      key=lambda p: p.stat().st_mtime)
    if not packages:
        r.add("EVIDENCE", "a previous session package exists", UNKNOWN,
              f"{sessions_root} holds no session packages")
        return
    latest = packages[-1]

    verifier = tools_dir / "decision_replay_verifier.py"
    if not verifier.exists():
        r.add("EVIDENCE", "the replay verifier is available", UNKNOWN,
              f"{verifier} is missing")
        return

    ok, out, err = _run([sys.executable, str(verifier), str(latest),
                         "--require-level", str(require_level)],
                        cwd=str(tools_dir.parent), timeout=120)
    achieved = ""
    for line in (out or "").splitlines():
        if line.startswith("STRONGEST HONEST REPLAY LEVEL:"):
            achieved = line.split(":", 1)[1].strip()
    if ok:
        r.add("EVIDENCE", f"the last session replays to level {require_level}", PASS,
              f"{latest.name} reached {achieved or 'the required level'}")
    elif achieved:
        r.add("EVIDENCE", f"the last session replays to level {require_level}", FAIL,
              f"{latest.name} reached only {achieved}",
              "run tools/decision_replay_verifier.py on it to see which level was "
              "blocked and why")
    else:
        r.add("EVIDENCE", f"the last session replays to level {require_level}", UNKNOWN,
              f"the verifier did not report a level for {latest.name}: {err[:150]}")


def assess(args) -> Readiness:
    r = Readiness()
    check_identity(r, Path(args.checkout), Path(args.branch_repo), args.expect_sha)
    check_inhibition(r, args.unit)
    check_configuration(r, Path(args.config))
    check_evidence(r, Path(args.sessions_root), args.require_replay_level,
                   Path(args.tools_dir))
    return r


def render(r: Readiness) -> str:
    lines = ["=" * 74, "PAPER SESSION READINESS", "=" * 74, ""]
    for group in ("IDENTITY", "INHIBITION", "CONFIGURATION", "EVIDENCE"):
        checks = [c for c in r.checks if c.group == group]
        if not checks:
            continue
        lines.append(group)
        for c in checks:
            lines.append(f"  [{c.status:^7}] {c.name}")
            lines.append(f"            {c.detail}")
            if c.remedy and c.status != PASS:
                lines.append(f"            -> {c.remedy}")
        lines.append("")
    lines += ["-" * 74, f"VERDICT: {r.verdict}"]
    if r.failed:
        lines.append(f"  {len(r.failed)} check(s) FAILED:")
        lines += [f"    - {c.name}" for c in r.failed]
    if r.unknown:
        lines.append(f"  {len(r.unknown)} check(s) returned UNKNOWN. An undetermined")
        lines.append("  check is not a satisfied one:")
        lines += [f"    - {c.name}" for c in r.unknown]
    if r.verdict == "READY":
        lines.append("  Every check passed. This is a statement about configuration and")
        lines.append("  evidence only -- it is not permission, and it is not a claim that")
        lines.append("  the market data feed works. That is Gate 1's question.")
    lines.append("-" * 74)
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--checkout", default="/opt/bujji/app")
    ap.add_argument("--branch-repo", default=".")
    ap.add_argument("--config", default="/opt/bujji/app/config/options_os_paper_trading.yaml")
    ap.add_argument("--sessions-root", default="/opt/bujji/app/shadow_sessions")
    ap.add_argument("--tools-dir", default="tools")
    ap.add_argument("--expect-sha", default=None,
                    help="the commit the operator intends to run; omitted means UNKNOWN")
    ap.add_argument("--unit", action="append", default=None,
                    help="systemd unit that must not be enabled (repeatable)")
    ap.add_argument("--require-replay-level", type=int, default=2)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    if args.unit is None:
        args.unit = ["bujji-options-os-trading.service",
                     "bujji-options-os-trading.timer"]

    try:
        r = assess(args)
    except Exception as exc:  # noqa: BLE001
        print(f"RUNTIME_ERROR: readiness assessment failed: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return EXIT_RUNTIME_ERROR

    if args.json:
        print(json.dumps({"verdict": r.verdict,
                          "checks": [vars(c) for c in r.checks]}, indent=2))
    else:
        print(render(r))

    if r.failed:
        return EXIT_REFUSED
    if r.unknown:
        return EXIT_PENDING_EVIDENCE
    return EXIT_READY


if __name__ == "__main__":
    raise SystemExit(main())
