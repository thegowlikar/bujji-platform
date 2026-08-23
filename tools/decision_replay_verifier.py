#!/usr/bin/env python3
"""Report the strongest HONEST level of decision replay a sealed session
evidence package actually supports.

READ-ONLY. This tool opens files and prints. It never writes to an evidence
package, never touches the live checkout, and never places, cancels or
values anything.

WHY A LEVEL, NOT A BOOLEAN. "Bujji can replay its decisions" is the kind of
claim that is easy to assert and hard to earn. Replay has strictly ordered
degrees, and a package supports exactly one of them:

  0 NO_EVIDENCE                  nothing readable to replay
  1 INPUT_INTEGRITY              the records exist, parse, and agree with each
                                 other about session, ordering and identity
  2 ANALYTICAL_REPRODUCIBILITY   the recorded market evidence re-derives the
                                 recorded market assessment
  3 ELIGIBILITY_REPRODUCIBILITY  the recorded assessment re-derives the
                                 recorded candidate set and selection
  4 FULL_DECISION_EQUIVALENCE    the recorded inputs re-derive the recorded
                                 orders -- strikes, legs and quantities

A level is only awarded when it is DEMONSTRATED by recomputation here, and
every level below it also holds. The tool always prints why it could not
award the next level up: a package that stops at 1 because the analytical
snapshot was never recorded is a different problem from one that stops at 1
because the recomputation disagreed, and the operator must be able to tell
those apart at a glance.

Exit codes follow the house convention:
  0 the package earned the level it claims
  1 CONFIG_ERROR    -- bad arguments or unreadable path
  2 RUNTIME_ERROR   -- the verifier itself failed
  4 PENDING_EVIDENCE -- the package is readable but supports a level below
                        what a clean paper session requires
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

EXIT_OK = 0
EXIT_CONFIG_ERROR = 1
EXIT_RUNTIME_ERROR = 2
EXIT_PENDING_EVIDENCE = 4

LEVEL_NAMES = {
    0: "NO_EVIDENCE",
    1: "INPUT_INTEGRITY",
    2: "ANALYTICAL_REPRODUCIBILITY",
    3: "ELIGIBILITY_REPRODUCIBILITY",
    4: "FULL_DECISION_EQUIVALENCE",
}

SELECTION_STAGE = "STRATEGY_SELECTION_EVALUATED"


@dataclass
class Finding:
    level: int
    ok: bool
    title: str
    detail: str


@dataclass
class Report:
    package: Path
    findings: List[Finding] = field(default_factory=list)
    blocked_because: Dict[int, str] = field(default_factory=dict)

    def add(self, level: int, ok: bool, title: str, detail: str) -> None:
        self.findings.append(Finding(level, ok, title, detail))

    def block(self, level: int, why: str) -> None:
        # First reason wins: the earliest thing that stopped us is the one to
        # fix, and a cascade of downstream complaints buries it.
        self.blocked_because.setdefault(level, why)

    @property
    def achieved(self) -> int:
        """The highest level for which every check passed AND no level at or
        below it is blocked. Strictly ordered -- level 3 does not count if
        level 2 could not even be attempted."""
        achieved = 0
        for lvl in (1, 2, 3, 4):
            checks = [f for f in self.findings if f.level == lvl]
            if lvl in self.blocked_because or not checks or not all(f.ok for f in checks):
                break
            achieved = lvl
        return achieved


def _read_jsonl(path: Path) -> Tuple[List[dict], List[str]]:
    records, errors = [], []
    if not path.exists():
        return records, [f"{path.name} is absent"]
    for n, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError as exc:
            errors.append(f"{path.name}:{n} is not valid JSON ({exc.msg})")
    return records, errors


def _explanation(rec: dict) -> dict:
    """Evidence records wrap the published payload in `explanation`; some
    older writers stored it flat. Accept both rather than silently reading
    an empty dict off the newer shape."""
    exp = rec.get("explanation")
    return exp if isinstance(exp, dict) else rec


# --------------------------------------------------------------------------
# Level 1 -- input integrity
# --------------------------------------------------------------------------
def check_input_integrity(pkg: Path, report: Report) -> Optional[List[dict]]:
    decisions_path = pkg / "decisions.jsonl"
    records, errors = _read_jsonl(decisions_path)

    if errors:
        report.add(1, False, "decision records parse", "; ".join(errors[:5]))
        report.block(2, "the decision records could not all be read")
        return None
    if not records:
        report.add(1, False, "decision records present",
                   f"{decisions_path.name} is empty -- there is nothing to replay")
        report.block(2, "no decision records exist")
        return None
    report.add(1, True, "decision records parse",
               f"{len(records)} record(s) read from {decisions_path.name}")

    # Identity agreement. A package whose records disagree about which session
    # they belong to cannot support a claim about that session.
    meta_path = pkg / "metadata.json"
    declared: Optional[str] = None
    if meta_path.exists():
        try:
            declared = json.loads(meta_path.read_text()).get("session_id")
        except json.JSONDecodeError as exc:
            report.add(1, False, "metadata parses", f"metadata.json is invalid JSON ({exc.msg})")

    seen = {_explanation(r).get("session_id") for r in records}
    seen.discard(None)
    if len(seen) > 1:
        report.add(1, False, "one session per package",
                   f"records claim {len(seen)} different session ids: {sorted(seen)}")
        report.block(2, "the package mixes more than one session")
    elif declared is not None and seen and declared not in seen:
        report.add(1, False, "records match the declared session",
                   f"metadata declares {declared!r} but records carry {sorted(seen)}")
        report.block(2, "the records do not belong to the declared session")
    else:
        report.add(1, True, "one session per package",
                   f"all records belong to {(sorted(seen) or [declared])[0]!r}")

    stamps = [r.get("timestamp") for r in records if r.get("timestamp")]
    if len(stamps) != len(records):
        report.add(1, False, "every record is timestamped",
                   f"{len(records) - len(stamps)} record(s) carry no timestamp")
    elif stamps != sorted(stamps):
        report.add(1, False, "records are in time order",
                   "timestamps are not monotonic -- the order of decisions is not trustworthy")
    else:
        report.add(1, True, "records are in time order",
                   f"{stamps[0]} .. {stamps[-1]}")

    # Provenance is recorded but currently empty. Report it as a finding, not
    # a failure: it does not stop a replay, and overstating it would push a
    # package below the level it genuinely earns.
    unowned = sum(1 for r in records if r.get("decision_owner") in (None, "", "UNKNOWN"))
    untraced = sum(1 for r in records if not r.get("trace_id"))
    if unowned or untraced:
        report.add(1, True, "provenance fields are present but unpopulated",
                   f"{unowned}/{len(records)} record(s) name no decision_owner and "
                   f"{untraced}/{len(records)} carry no trace_id. Replay does not need "
                   f"them; correlating this session with another store does.")
    return records


# --------------------------------------------------------------------------
# Level 2 -- analytical reproducibility
# --------------------------------------------------------------------------
def check_analytical_reproducibility(pkg, records, report):
    """Does the recorded selection resolve to the market assessment it came
    from, and do the two AGREE about the regime that was handed over?

    This is a cross-file check, not a presence check. A reference that points
    at nothing, or at a thesis that recorded a different regime than the
    selection acted on, is worse than no reference: it looks like provenance
    while contradicting the decision it claims to explain."""
    selections = [r for r in records if _explanation(r).get("stage") == SELECTION_STAGE]
    if not selections:
        report.block(2, f"no {SELECTION_STAGE} record exists in this package")
        return

    theses, errors = _read_jsonl(pkg / "market_thesis.jsonl")
    if errors and not theses:
        report.block(2, f"the market assessment record is absent or unreadable "
                        f"({'; '.join(errors[:2])})")
        return

    by_id = {}
    for t in theses:
        aid = (t.get("thesis") or {}).get("assessment_id")
        if aid:
            by_id[aid] = t

    unreferenced = [s for s in selections
                    if not _explanation(s).get("analytical_snapshot_ref")]
    if unreferenced:
        report.block(
            2,
            f"{len(unreferenced)}/{len(selections)} selection record(s) name no "
            f"analytical_snapshot_ref, so the regime labels they acted on cannot be "
            f"traced to the assessment that produced them. The package records both "
            f"halves and no link between them.")
        return

    for i, rec in enumerate(selections):
        exp = _explanation(rec)
        ref = exp.get("analytical_snapshot_ref")
        thesis = by_id.get(ref)
        if thesis is None:
            report.add(2, False, f"selection #{i + 1} resolves its assessment",
                       f"analytical_snapshot_ref {ref!r} matches no assessment_id in "
                       f"market_thesis.jsonl ({len(by_id)} available)")
            continue
        handed = thesis.get("regime_handed_to_selector") or {}
        if (handed.get("trend_regime") != exp.get("trend_regime")
                or handed.get("volatility_regime") != exp.get("volatility_regime")):
            report.add(2, False, f"selection #{i + 1} agrees with its assessment",
                       f"the assessment handed over "
                       f"({handed.get('trend_regime')}, {handed.get('volatility_regime')}) "
                       f"but the selection acted on "
                       f"({exp.get('trend_regime')}, {exp.get('volatility_regime')})")
            continue
        report.add(2, True, f"selection #{i + 1} resolves and agrees",
                   f"{ref} handed ({handed.get('trend_regime')}, "
                   f"{handed.get('volatility_regime')})")


# --------------------------------------------------------------------------
# Level 3 -- eligibility reproducibility
# --------------------------------------------------------------------------
def check_eligibility_reproducibility(records: List[dict], report: Report) -> None:
    """Re-run the real selector over the recorded regime and compare. This is
    genuine recomputation against the module the runtime uses -- not a
    re-reading of what was written down."""
    try:
        from bujji.production_runtime.trading_session_governor import strategy_selector as ss
    except Exception as exc:  # noqa: BLE001
        report.block(3, f"the selector could not be imported for recomputation: {exc}")
        return

    selections = [r for r in records if _explanation(r).get("stage") == SELECTION_STAGE]
    if not selections:
        report.block(3, f"no {SELECTION_STAGE} record exists in this package")
        return

    for i, rec in enumerate(selections):
        exp = _explanation(rec)
        trend, vol = exp.get("trend_regime"), exp.get("volatility_regime")
        recorded_selection = exp.get("selected_strategy")
        recorded_candidates = exp.get("candidates")

        # The risk mode is DERIVED from the execution mode and is not written
        # into the record. Rather than guess it, replay under both and accept
        # a match in either -- and say so, so nobody reads this as a stronger
        # result than it is.
        matches = []
        for defined_risk_only in (False, True):
            declared = ss.eligible_families(
                trend, vol, defined_risk_only=defined_risk_only)
            expected = declared[0] if declared else None
            if expected == recorded_selection:
                matches.append(defined_risk_only)

        if not matches:
            report.add(3, False, f"selection #{i + 1} recomputes",
                       f"recorded ({trend}, {vol}) -> {recorded_selection!r}, but the "
                       f"selector re-derives something else in BOTH risk modes")
            continue

        mode_note = ("either risk mode" if len(matches) == 2
                     else f"defined_risk_only={matches[0]}")
        report.add(3, True, f"selection #{i + 1} recomputes",
                   f"({trend}, {vol}) -> {recorded_selection!r}, reproduced under {mode_note}")

        if recorded_candidates is None:
            report.add(3, True, f"selection #{i + 1} candidate set",
                       "the record predates candidate recording; the SELECTION was "
                       "reproduced but the set of rejected alternatives was not")
            continue

        replayed = {
            c.family: c.reason_code
            for c in ss._evaluate_candidates(trend, vol, matches[0])
        }
        recorded = {c["family"]: c["reason_code"] for c in recorded_candidates}
        if replayed != recorded:
            differing = sorted(set(replayed) | set(recorded))
            diffs = [f"{f}: recorded {recorded.get(f)!r} vs replayed {replayed.get(f)!r}"
                     for f in differing if recorded.get(f) != replayed.get(f)]
            report.add(3, False, f"selection #{i + 1} candidate set recomputes",
                       "; ".join(diffs[:4]))
        else:
            report.add(3, True, f"selection #{i + 1} candidate set recomputes",
                       f"all {len(recorded)} candidate verdicts reproduced exactly")


# --------------------------------------------------------------------------
# Level 4 -- full decision equivalence
# --------------------------------------------------------------------------
def check_full_decision_equivalence(pkg: Path, report: Report) -> None:
    orders_path = pkg / "orders.jsonl"
    if not orders_path.exists():
        report.block(4, "the package records no orders; there is no execution to "
                        "re-derive (a no-trade session can never exceed level 3)")
        return
    records, errors = _read_jsonl(orders_path)
    if errors or not records:
        report.block(4, "the order records are absent or unreadable")
        return
    report.block(
        4,
        f"{len(records)} order record(s) exist, but the option chain snapshot the "
        f"strikes were chosen from is not part of the evidence package. Strikes "
        f"cannot be re-derived from a book that was never written down.")


def verify(pkg: Path) -> Report:
    report = Report(package=pkg)
    records = check_input_integrity(pkg, report)
    if records is None:
        return report
    check_analytical_reproducibility(pkg, records, report)
    check_eligibility_reproducibility(records, report)
    check_full_decision_equivalence(pkg, report)
    return report


def render(report: Report) -> str:
    lines = [
        "=" * 74,
        f"DECISION REPLAY VERIFICATION -- {report.package.name}",
        "=" * 74,
        "",
    ]
    for lvl in (1, 2, 3, 4):
        checks = [f for f in report.findings if f.level == lvl]
        blocked = report.blocked_because.get(lvl)
        if not checks and not blocked:
            continue
        lines.append(f"LEVEL {lvl} -- {LEVEL_NAMES[lvl]}")
        for f in checks:
            lines.append(f"  [{'PASS' if f.ok else 'FAIL'}] {f.title}")
            lines.append(f"         {f.detail}")
        if blocked:
            lines.append(f"  [ -- ] not attempted")
            lines.append(f"         {blocked}")
        lines.append("")

    achieved = report.achieved
    lines += [
        "-" * 74,
        f"STRONGEST HONEST REPLAY LEVEL: {achieved} ({LEVEL_NAMES[achieved]})",
    ]
    nxt = achieved + 1
    if nxt in LEVEL_NAMES:
        why = report.blocked_because.get(nxt)
        if why is None:
            failed = [f for f in report.findings if f.level == nxt and not f.ok]
            why = failed[0].detail if failed else "the checks for the next level did not pass"
        lines.append(f"WHY NOT {nxt} ({LEVEL_NAMES[nxt]}): {why}")
    lines.append("-" * 74)
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("package", help="path to a sealed session evidence directory")
    ap.add_argument("--require-level", type=int, default=None,
                    help="exit 4 (PENDING_EVIDENCE) if the package does not reach this level")
    args = ap.parse_args()

    pkg = Path(args.package)
    if not pkg.is_dir():
        print(f"CONFIG_ERROR: {pkg} is not a directory", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    try:
        report = verify(pkg)
    except Exception as exc:  # noqa: BLE001
        print(f"RUNTIME_ERROR: the verifier itself failed: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return EXIT_RUNTIME_ERROR

    print(render(report))
    if args.require_level is not None and report.achieved < args.require_level:
        print(f"\nPENDING_EVIDENCE: required level {args.require_level} "
              f"({LEVEL_NAMES.get(args.require_level, '?')}), achieved {report.achieved}.")
        return EXIT_PENDING_EVIDENCE
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
