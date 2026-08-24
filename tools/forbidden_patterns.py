"""Universe patterns that must never define what production trades.

WHAT THESE PATTERNS HAVE IN COMMON. Each one lets some component decide, on its
own, which contracts exist or what they are called -- instead of deriving that
from the canonical universe model (`bujji.capture_universe.builder`). Two
components that each answer "which contracts?" will eventually answer
differently, and the difference surfaces as a strike nobody can price, a symbol
the venue rejects, or a reconciliation that silently matches nothing.

  expiry-by-list-order   Taking a broker response's expiry list by POSITION.
                         Assumes the broker sorts, and that the rows returned
                         belong to whichever entry sits first. Neither is
                         verified by the broker's contract.

  fixed-strike-count     A hardcoded number of strikes. The real symbol master
                         steps by 50 near expiry and 1500 for LEAPS, so "N
                         strikes each side" means a different width on
                         different expiries -- and a different width from the
                         universe that was actually subscribed.

  broker-symbol-built    Constructing a venue symbol from parts rather than
                         selecting a real one. A symbol that does not exist
                         cannot be priced, ordered, or reconciled, and the
                         failure is silent: it looks like an absent position.

  independent-atm        Computing an at-the-money strike locally. The ATM the
                         universe was centred on is the only one whose band is
                         actually subscribed.

DOCSTRINGS ARE STRIPPED BEFORE MATCHING, and that is not cosmetic. The first
version of this detector reported five false positives in reachable code, every
one of them PROSE -- comments and docstrings describing a defect that had
already been fixed, including this repository's own explanations of why
`expiryData[0]` is forbidden. A detector that cannot tell an instruction from a
description produces noise, and a noisy ratchet gets disabled.

This tool REPORTS. It does not judge whether a given instance is acceptable --
`ARCHITECTURE.md` does that, and `tests/test_architecture_contract.py` enforces
what that document declares.
"""
from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path
from typing import Dict, List, Tuple

PATTERNS: Dict[str, re.Pattern] = {
    "expiry-by-list-order": re.compile(
        r"expiryData\s*\[\s*0\s*\]|expiry_entries\s*\[\s*0\s*\]|expiry_list\s*\[\s*0\s*\]"),
    "fixed-strike-count": re.compile(
        r"strike_count\s*=\s*\d+|strikes_each_side\s*=\s*\d+"
        r"|STRIKES_EACH_SIDE\s*=\s*\d+|strikecount\s*=\s*\d+"),
    # `{underlying}` joined directly to a strike or expiry with no separator is
    # a VENUE symbol. A pipe-delimited identity (`NIFTY|2026-08-25|24000|CE`) is
    # this codebase's canonical internal identity and is deliberately excluded.
    # `underlying[a-z_]*` and not a bare `underlying`: the first version
    # matched the literal placeholder name, so
    # `f"{underlying_symbol}{int(leg.strike)}{leg.option_type}"` in
    # bujji/shadow_lifecycle/orchestrator.py -- the same defect, same shape,
    # one identifier longer -- was invisible to it. A detector that matches a
    # VARIABLE NAME rather than a SHAPE is defeated by renaming, which is the
    # one edit most likely to happen by accident.
    "broker-symbol-built": re.compile(
        r"""f["'][^"'|]*\{underlying[a-z_]*\}[^"'|]*\{[^"']*(strike|expiry)"""),
    "independent-atm": re.compile(
        r"def\s+_atm_strike|def\s+_atm\b|def\s+_atm_strikes_grid"
        r"|round\([^)]*\/\s*50\s*\)\s*\*\s*50"),
}


class _StripDocstrings(ast.NodeTransformer):
    def _strip(self, node):
        self.generic_visit(node)
        body = getattr(node, "body", None)
        if (body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            node.body = body[1:] or [ast.Pass()]
        return node

    visit_Module = _strip
    visit_ClassDef = _strip
    visit_FunctionDef = _strip
    visit_AsyncFunctionDef = _strip


def executable_source(path: Path) -> str:
    """The file's CODE, with docstrings removed. Comments are already absent
    from the AST, so unparsing yields executable statements only."""
    try:
        tree = _StripDocstrings().visit(ast.parse(
            path.read_text(encoding="utf-8", errors="replace")))
        ast.fix_missing_locations(tree)
        return ast.unparse(tree)
    except Exception:  # noqa: BLE001 -- an unparseable file carries no verdict
        return ""


def scan_source(code: str) -> List[Tuple[str, str]]:
    """[(pattern_name, offending_line)] for one module's executable source."""
    found: List[Tuple[str, str]] = []
    for name, rx in PATTERNS.items():
        match = rx.search(code)
        if not match:
            continue
        start = code.rfind("\n", 0, match.start()) + 1
        end = code.find("\n", match.end())
        line = code[start: end if end != -1 else len(code)].strip()
        found.append((name, line[:140]))
    return found


def scan_repo(root: Path) -> Dict[str, List[Tuple[str, str]]]:
    """production module -> its forbidden patterns. Tests are not scanned:
    a test that constructs a symbol is exercising a defence, not committing one."""
    sys.path.insert(0, str(root / "tools"))
    import reachability  # noqa: E402

    out: Dict[str, List[Tuple[str, str]]] = {}
    for name, path in reachability.module_map(root).items():
        if name.startswith("tests"):
            continue
        hits = scan_source(executable_source(path))
        if hits:
            out[name] = hits
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Report forbidden universe patterns.")
    ap.add_argument("--root", default=str(Path(__file__).resolve().parent.parent))
    args = ap.parse_args()
    root = Path(args.root)

    sys.path.insert(0, str(root / "tools"))
    import reachability  # noqa: E402

    reachable = set(reachability.analyse(root)["reachable_modules"])
    hits = scan_repo(root)

    live = {m: h for m, h in hits.items() if m in reachable}
    dormant = {m: h for m, h in hits.items() if m not in reachable}

    print("FORBIDDEN UNIVERSE PATTERNS")
    print("=" * 62)
    print(f"  reachable from production : {len(live):3d} module(s)")
    print(f"  dormant (not reachable)   : {len(dormant):3d} module(s)")
    print()
    print("REACHABLE — these define what production trades")
    for module in sorted(live):
        for kind, line in live[module]:
            print(f"  {module}\n      [{kind}] {line}")
    print()
    print("DORMANT — cannot affect production today; must not become reachable")
    for module in sorted(dormant):
        print(f"  {module:56s} {', '.join(k for k, _ in dormant[module])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
