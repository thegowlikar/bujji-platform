"""Which modules can production actually reach?  **PROVISIONAL.**

STATUS: PROVISIONAL. This tool has had FOUR material corrections, each found by
a positive control rather than by review, and each changing the headline figure:

    19.0%  ->  relative imports were not followed at all
    35.6%  ->  package `__init__` execution was not counted as reached
    38.2%  ->  relative imports inside `__init__.py` anchored on the PARENT
               package, silently resolving `.journal` to an unrelated real
               module -- WRONG edges, not merely missing ones
    43.1%  ->  current

Three of those inflated the "orphaned" side, which is the direction that
flatters any claim built on this output. Until it has FIXTURE-BASED tests
covering absolute imports, package-relative imports at every level, `__init__`
anchoring, namespace/package ambiguity, and all eight real systemd entry
points, treat its numbers as INDICATIVE.

It is sound enough to answer "is this specific module reachable?" -- a question
whose answer can be confirmed by grep in seconds, and which the quarantine
ratchet uses. It is NOT yet a reliable source for strong quantitative claims,
and no safety argument should rest on its percentages.

WHY THIS IS A COMMITTED TOOL AND NOT A ONE-OFF SCRIPT.

Measured 2026-08-22: of 1,261 production modules, 243 are reachable from the
eight entry points systemd actually runs. 748 of the remainder are imported
ONLY by tests. That ratio is the single most useful fact about this repository,
because it explains how a green suite has repeatedly coexisted with a broken
runtime -- most of what the suite exercises is not what runs.

REACHABLE and TEST-ONLY are CLASSIFICATIONS, not verdicts. A test-only module
is not garbage and this tool never recommends deleting one. It means exactly
one thing: **that module cannot support a claim about production safety.** A
passing test over a test-only module proves the module works; it proves nothing
about the system that trades. Deciding what to do with such a module -- wire
it, keep it as a library for future work, or retire it -- is an engineering
judgement this tool deliberately does not make.

METHOD. Static import graph over every `.py` in the repo, walked transitively
from the entry points below. Dotted imports are resolved to the longest prefix
that names a real module, so `from a.b.c import D` reaches `a.b.c` (or `a.b`,
or `a`) as appropriate.

KNOWN LIMITS, stated because a reachability claim that hides them is worse than
none. Dynamic imports via `importlib`, `__import__`, or a string in config are
NOT followed, so a module reached only that way is reported as orphaned. The
counts are therefore a LOWER bound on reachability. Anything this tool calls
reachable really is; anything it calls orphaned should be confirmed by grep
before acting on it.
"""
from __future__ import annotations

import argparse
import ast
import collections
import json
import sys
from pathlib import Path
from typing import Dict, Iterable, Set

# The eight units systemd actually starts. Kept as literals, in one place, so a
# new unit that is never added here shows up as a shrinking reachable set
# rather than as silence.
ENTRY_POINTS = (
    "bujji_options_os_runner",                  # bujji-options-os-trading.service
    "run_daily_intelligence_session",           # bujji-daily-intelligence.service
    "bujji.app",                                # bujji-shadow-decision-campaign.service
    "run_live_shadow",
    "scripts.run_paper_intelligence_campaign",
    "scripts.refresh_price_levels",             # bujji-price-levels.service
    "scripts.run_futures_depth_poller",         # bujji-futures-depth-poller.service
    "scripts.backup_observation_stores",        # bujji-backup.service
)

_SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv"}


def module_map(root: Path) -> Dict[str, Path]:
    out: Dict[str, Path] = {}
    for path in root.rglob("*.py"):
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        rel = path.relative_to(root)
        if rel.name == "__init__.py":
            name = ".".join(rel.parts[:-1])
        else:
            name = ".".join(rel.parts)[:-3]
        if name:
            out[name] = path
    return out


def _package_of(module: str) -> str:
    """The package a module lives in. `a.b.c` -> `a.b`; a top-level module -> ""."""
    return module.rsplit(".", 1)[0] if "." in module else ""


def imports_of(path: Path, module: str, is_package: bool = False) -> Set[str]:
    """Absolute module names this file imports, RELATIVE IMPORTS INCLUDED.

    The first version of this function skipped `node.level > 0` entirely, and a
    positive control caught it: `session_governor` showed as reachable while
    `session_trading_state` and `strategy_lock` -- which it imports as
    `from .strategy_lock import ...` -- showed as unreachable. `bujji/` uses
    relative imports throughout its packages, so that omission silently cut
    every intra-package edge and undercounted reachability badly.

    A reachability tool that cannot see half the graph is worse than no tool,
    because its output looks authoritative.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:  # noqa: BLE001 -- an unparseable file has no edges, not no entry
        return set()

    found: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                base = node.module or ""
            else:
                # WHERE A RELATIVE IMPORT ANCHORS DEPENDS ON WHAT THE FILE IS.
                #
                # In a MODULE `a.b.c`, `from .d import x` means `a.b.d` -- one
                # level up. In a PACKAGE's `__init__.py`, whose module name is
                # `a.b`, the same statement means `a.b.d` -- the package ITSELF,
                # not its parent.
                #
                # Treating both the same produced WRONG EDGES, not merely
                # missing ones: `from .journal import ...` inside
                # `bujji/tick_journal/__init__.py` resolved to `bujji.journal`,
                # a real and entirely unrelated package. The graph then showed
                # a dependency that does not exist and missed the one that
                # does. Found when types in a freshly-wired package reported as
                # unreachable while the runner plainly imported them.
                base = module if is_package else _package_of(module)
                for _ in range(node.level - 1):
                    base = _package_of(base)
                if node.module:
                    base = f"{base}.{node.module}" if base else node.module
            if not base:
                continue
            found.add(base)
            for alias in node.names:
                found.add(f"{base}.{alias.name}")
    return found


def _resolve(dep: str, graph: Dict[str, Set[str]]) -> str | None:
    """Longest prefix of `dep` that names a real module in this repo."""
    candidate = dep
    while candidate:
        if candidate in graph:
            return candidate
        if "." not in candidate:
            return None
        candidate = candidate.rsplit(".", 1)[0]
    return None


def _with_package_ancestors(module: str, graph: Dict[str, Set[str]]) -> Set[str]:
    """`a.b.c` plus every package along the way that exists as a module.

    Importing `a.b.c` EXECUTES `a/__init__.py` and `a/b/__init__.py`. A graph
    that marked only the leaf reported those packages as unreachable while
    Python was running their code every time -- which is not a nuance, it is a
    module claiming to be dormant while executing in production.

    Found when `bujji.capture_universe` moved from test-only to UNREFERENCED
    merely because a test started importing `bujji.capture_universe.builder`
    instead of the package. Nothing about the package's participation had
    changed; the graph was simply wrong about it.
    """
    out = {module}
    parts = module.split(".")
    for i in range(1, len(parts)):
        prefix = ".".join(parts[:i])
        if prefix in graph:
            out.add(prefix)
    return out


def reachable_from(entries: Iterable[str], graph: Dict[str, Set[str]]) -> Set[str]:
    seen: Set[str] = set()
    stack: list[str] = []
    for entry in entries:
        if entry in graph:
            stack.extend(_with_package_ancestors(entry, graph))
    while stack:
        module = stack.pop()
        if module in seen:
            continue
        seen.add(module)
        for dep in graph[module]:
            target = _resolve(dep, graph)
            if not target:
                continue
            for name in _with_package_ancestors(target, graph):
                if name not in seen:
                    stack.append(name)
    return seen


def analyse(root: Path) -> dict:
    mods = module_map(root)
    graph = {name: imports_of(path, name, is_package=(path.name == "__init__.py"))
             for name, path in mods.items()}

    missing = [e for e in ENTRY_POINTS if e not in mods]
    reach_all = reachable_from(ENTRY_POINTS, graph)

    tests = {m for m in mods if m.startswith("tests")}
    production = set(mods) - tests
    reachable = reach_all & production
    orphaned = production - reachable

    test_imports: Set[str] = set()
    for t in tests:
        for dep in graph[t]:
            target = _resolve(dep, graph)
            if target:
                test_imports.add(target)

    test_only = orphaned & test_imports
    unreferenced = orphaned - test_imports

    return {
        "entry_points": list(ENTRY_POINTS),
        "entry_points_missing": missing,
        "totals": {
            "python_files": len(mods),
            "test_modules": len(tests),
            "production_modules": len(production),
            "reachable": len(reachable),
            "orphaned": len(orphaned),
            "test_only": len(test_only),
            "unreferenced": len(unreferenced),
        },
        "reachable_modules": sorted(reachable),
        "test_only_modules": sorted(test_only),
        "unreferenced_modules": sorted(unreferenced),
    }


def render(report: dict) -> str:
    t = report["totals"]
    lines = [
        "BUJJI RUNTIME REACHABILITY",
        "=" * 62,
        "",
        "Reachable = imported, transitively, from a module systemd starts.",
        "Test-only = imported by tests but by nothing production reaches.",
        "            It CANNOT support a production-safety claim. That is a",
        "            classification, not a recommendation to delete it.",
        "",
        f"  python files          {t['python_files']:6d}",
        f"    test modules        {t['test_modules']:6d}",
        f"    production modules  {t['production_modules']:6d}",
        "",
        f"  REACHABLE             {t['reachable']:6d}"
        f"   ({100.0 * t['reachable'] / max(1, t['production_modules']):.1f}% of production)",
        f"  orphaned              {t['orphaned']:6d}",
        f"    test-only           {t['test_only']:6d}",
        f"    unreferenced        {t['unreferenced']:6d}",
        "",
    ]
    if report["entry_points_missing"]:
        lines += ["  !! ENTRY POINTS NOT FOUND: " + ", ".join(report["entry_points_missing"]), ""]
    pkg = collections.Counter(m.split(".")[0] for m in report["reachable_modules"])
    lines.append("REACHABLE BY TOP-LEVEL PACKAGE")
    for name, count in pkg.most_common():
        lines.append(f"  {name:44s} {count:5d}")
    return "\n".join(lines) + "\n"


def architecture_block(report: dict) -> str:
    """The fenced figures block as ARCHITECTURE.md carries it."""
    t = report["totals"]
    pct = 100.0 * t["reachable"] / max(1, t["production_modules"])
    return (
        "```\n"
        f"python files            {t['python_files']}\n"
        f"  test modules           {t['test_modules']}\n"
        f"  production modules    {t['production_modules']}\n"
        "\n"
        f"REACHABLE                {t['reachable']}   ({pct:.1f}% of production)\n"
        f"orphaned                 {t['orphaned']}\n"
        f"  test-only              {t['test_only']}\n"
        f"  unreferenced           {t['unreferenced']}\n"
        "```"
    )


def write_architecture(root: Path, report: dict) -> None:
    """Rewrite the figures block in ARCHITECTURE.md in place.

    The document is enforced -- `test_the_reachability_figures_in_the_document_
    are_current` fails when it drifts -- so regenerating it has to be one
    command rather than a hand edit. A living document nobody can cheaply
    refresh becomes a stale document.
    """
    path = root / "ARCHITECTURE.md"
    text = path.read_text(encoding="utf-8")
    start = text.index("```\npython files")
    end = text.index("```", text.index("unreferenced", start)) + 3
    path.write_text(text[:start] + architecture_block(report) + text[end:], encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=str(Path(__file__).resolve().parent.parent))
    ap.add_argument("--json", action="store_true", help="emit the full report as JSON")
    ap.add_argument("--write-architecture", action="store_true",
                    help="rewrite the figures block in ARCHITECTURE.md in place")
    args = ap.parse_args()
    root = Path(args.root)
    report = analyse(root)
    if args.write_architecture:
        write_architecture(root, report)
        t = report["totals"]
        pct = 100.0 * t["reachable"] / max(1, t["production_modules"])
        sys.stdout.write(
            f"ARCHITECTURE.md updated: {t['reachable']}/{t['production_modules']} "
            f"({pct:.1f}%) reachable\n")
        return 0
    if args.json:
        json.dump(report, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        sys.stdout.write(render(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
