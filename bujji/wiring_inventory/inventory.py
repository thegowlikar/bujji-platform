"""What actually runs, and what only exists.

THE DEFECT THIS MEASURES. This project's recurring failure is not bad code,
it is code that passes its own definition of done -- built, tested,
documented -- and is then never connected to anything that executes.
Discovered by hand on 2026-08-17, in one afternoon:

  * `market_timeseries` -- full SQLite store, aggregator, tests. Its own
    docstring concedes "no market_timeseries `.db` file has ever existed
    on disk". Confirmed: none does.
  * `market_microstructure` -- store, models, aggregator, tests. No
    database. And `scripts/run_eod_reconciliation.py` READS from it: a
    consumer whose producer was never written.
  * `hydrate_paper_broker`, two memory-similarity engines,
    `ShadowLifecycleOrchestrator` -- zero callers outside tests.
  * The Gate 1 harness -- built, safety-verified, universe validated
    against the real symbol master, never run.

Each was invisible because "does it have callers?" was never asked
automatically. This asks it, every day, and prints one number.

WHAT "REACHABLE" MEANS HERE. Not "someone imports it" -- a package
imported only by another dormant package is still dormant. Reachability is
computed transitively from the entrypoints SYSTEMD ACTUALLY RUNS, read out
of `deploy/*.service` ExecStart lines. That definition cannot drift from
production, because it IS production: if no scheduled unit can reach it, it
does not run, whatever else imports it.

DORMANCY IS ALLOWED, BUT MUST BE DECLARED. Some code legitimately waits for
a phase that has not arrived. That is fine -- what is not fine is
discovering it eighteen months later. A package may set `__dormant__` in
its `__init__.py` to a reason string; the report then counts it as
declared rather than surprising. Read via AST, never by importing, so
scanning stays side-effect free.

READ-ONLY. This module imports nothing from the packages it analyses and
executes none of them.
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple

# Directories that are never production, whatever imports them.
_NON_PRODUCTION = ("tests", "__pycache__", ".git", "qualification", "shadow_sessions")

_EXEC_PY = re.compile(r"(\S+\.py)\b")
_EXEC_MODULE = re.compile(r"-m\s+([A-Za-z_][\w.]*)")


@dataclass(frozen=True)
class PackageStatus:
    package: str
    modules: int
    importers: int                 # distinct production modules importing it
    reachable: bool                # transitively, from a systemd entrypoint
    dormant_reason: Optional[str]  # declared via __dormant__
    persists_to_sqlite: bool

    @property
    def is_dormant(self) -> bool:
        return not self.reachable

    @property
    def is_undeclared_dormant(self) -> bool:
        return self.is_dormant and self.dormant_reason is None


@dataclass
class InventoryReport:
    packages: List[PackageStatus] = field(default_factory=list)
    entrypoints: List[str] = field(default_factory=list)
    orphan_consumers: List[Tuple[str, str]] = field(default_factory=list)

    @property
    def dormant(self) -> List[PackageStatus]:
        return [p for p in self.packages if p.is_dormant]

    @property
    def undeclared_dormant(self) -> List[PackageStatus]:
        return [p for p in self.packages if p.is_undeclared_dormant]

    @property
    def headline(self) -> int:
        """The one number. Undeclared dormant packages -- code that exists,
        runs nowhere, and never said so."""
        return len(self.undeclared_dormant)


# --------------------------------------------------------------- imports --
def module_name_for(path: Path, root: Path) -> str:
    rel = path.relative_to(root).with_suffix("")
    parts = list(rel.parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def imports_of(path: Path, root: Path) -> Set[str]:
    """Absolute module names this file imports, relative imports resolved.

    A file that cannot be parsed yields nothing rather than raising: a
    syntax error somewhere in the tree must not blind the whole report.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except (SyntaxError, ValueError, OSError):
        return set()

    own = module_name_for(path, root)
    own_parts = own.split(".")
    package_parts = own_parts[:-1] if path.name != "__init__.py" else own_parts

    found: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package_parts[: len(package_parts) - (node.level - 1)]
                target = ".".join([*base, node.module]) if node.module else ".".join(base)
            else:
                target = node.module or ""
            if target:
                found.add(target)
    return found


def production_files(root: Path) -> List[Path]:
    out = []
    for path in root.rglob("*.py"):
        rel = path.relative_to(root)
        if any(part in _NON_PRODUCTION for part in rel.parts):
            continue
        out.append(path)
    return sorted(out)


# ----------------------------------------------------------- entrypoints --
def entrypoints_from_units(deploy_dir: Path, root: Path) -> List[str]:
    """Module names systemd actually starts, read from ExecStart.

    Handles both `python path/to/runner.py` and `python -m bujji.app`.
    """
    names: Set[str] = set()
    if not deploy_dir.is_dir():
        return []
    for unit in sorted(deploy_dir.glob("*.service")):
        for line in unit.read_text().splitlines():
            if not line.startswith("ExecStart="):
                continue
            module_match = _EXEC_MODULE.search(line)
            if module_match:
                names.add(module_match.group(1))
                continue
            for candidate in _EXEC_PY.findall(line):
                path = Path(candidate)
                stem = path.name[:-3]
                # Prefer a real file so the name matches the import graph.
                for probe in (root / candidate.lstrip("/"), root / path.name):
                    if probe.exists():
                        names.add(module_name_for(probe, root))
                        break
                else:
                    names.add(stem)
    return sorted(names)


_SCRIPT_REF = re.compile(r"['\"]([\w/]*scripts/[\w./-]+\.py)['\"]")


def subprocess_entrypoints(root: Path, modules: Sequence[str], files: Sequence[Path]) -> Set[str]:
    """Scripts a reachable module launches as a SUBPROCESS, not an import.

    Without this the whole capture path reads as dormant, which is simply
    wrong: `run_daily_intelligence_session.py` runs
    `scripts/capture_market_reality_session.py` via subprocess and waits on
    it for the entire session. Import edges cannot see that, and a report
    that mislabels live production code as dead teaches people to ignore it.
    """
    by_module = {module_name_for(p, root): p for p in files}
    found: Set[str] = set()
    for module in modules:
        path = by_module.get(module)
        if path is None:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for reference in _SCRIPT_REF.findall(text):
            probe = root / reference.lstrip("/")
            if probe.exists():
                found.add(module_name_for(probe, root))
    return found


def reachable_modules(graph: Dict[str, Set[str]], entrypoints: Sequence[str]) -> Set[str]:
    seen: Set[str] = set()
    stack = [e for e in entrypoints]
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        for target in graph.get(current, ()):  # noqa: SIM118
            if target in seen:
                continue
            # An import of `bujji.a.b` also reaches package `bujji.a`.
            stack.append(target)
            parts = target.split(".")
            for i in range(1, len(parts)):
                stack.append(".".join(parts[:i]))
    return seen


# --------------------------------------------------------------- dormancy --
def declared_dormancy(package_dir: Path) -> Optional[str]:
    """Read `__dormant__` from a package's __init__.py via AST.

    Never imports the package: scanning must not execute the code it is
    auditing.
    """
    init = package_dir / "__init__.py"
    if not init.exists():
        return None
    try:
        tree = ast.parse(init.read_text(encoding="utf-8", errors="replace"))
    except (SyntaxError, ValueError, OSError):
        return None
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "__dormant__":
                    if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                        return node.value.value
    return None


# ---------------------------------------------------------------- storage --
# Assembled from fragments on purpose: written whole, these literals would
# appear in THIS file and the scanner would report its own package as
# defining a store. It did exactly that on the first run.
_SQLITE_MARKERS = ("sqlite3." + "connect", "CREATE " + "TABLE")


def persists_to_sqlite(paths: Sequence[Path]) -> bool:
    """Does this package create or open a SQLite database anywhere?

    A DORMANT package that defines a store is the sharpest signal in the
    report: its database cannot contain a single row, because nothing that
    runs can reach the code that writes it.
    """
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if any(marker in text for marker in _SQLITE_MARKERS):
            return True
    return False


# ----------------------------------------------------------------- report --
def build_inventory(root: Path, *, package_root: str = "bujji") -> InventoryReport:
    files = production_files(root)
    graph: Dict[str, Set[str]] = {}
    for path in files:
        graph[module_name_for(path, root)] = imports_of(path, root)

    entrypoints = entrypoints_from_units(root / "deploy", root)
    # Subprocess launches are real production edges the import graph cannot
    # see. Iterate to a fixed point: a script reached this way may itself
    # launch another.
    reachable = reachable_modules(graph, entrypoints)
    while True:
        launched = subprocess_entrypoints(root, sorted(reachable), files)
        if launched <= set(entrypoints):
            break
        entrypoints = sorted(set(entrypoints) | launched)
        reachable = reachable_modules(graph, entrypoints)

    pkg_dir = root / package_root
    packages: List[PackageStatus] = []

    for child in sorted(p for p in pkg_dir.iterdir() if p.is_dir()):
        if child.name in _NON_PRODUCTION:
            continue
        name = f"{package_root}.{child.name}"
        members = [p for p in files if module_name_for(p, root).startswith(name)]
        if not members:
            continue
        importers = {
            module_name_for(p, root) for p in files
            if not module_name_for(p, root).startswith(name)
            and any(imp == name or imp.startswith(name + ".") for imp in graph[module_name_for(p, root)])
        }
        stores = persists_to_sqlite(members)
        packages.append(PackageStatus(
            package=name, modules=len(members), importers=len(importers),
            reachable=any(module_name_for(p, root) in reachable for p in members) or name in reachable,
            dormant_reason=declared_dormancy(child),
            persists_to_sqlite=stores,
        ))

    # ORPHAN CONSUMER: code that RUNS, reading a package that never does.
    # The consumer must itself be reachable -- a dormant script importing a
    # dormant package is simply more dormant code, and listing those buries
    # the one case that actually matters (a live path depending on output
    # nothing produces, e.g. run_eod_reconciliation -> market_microstructure).
    dormant_names = {p.package for p in packages if p.is_dormant}
    orphans: List[Tuple[str, str]] = []
    for path in files:
        module = module_name_for(path, root)
        if module not in reachable:
            continue
        if any(module == d or module.startswith(d + ".") for d in dormant_names):
            continue
        for imported in graph[module]:
            for dormant in dormant_names:
                if imported == dormant or imported.startswith(dormant + "."):
                    orphans.append((module, dormant))
    return InventoryReport(packages=packages, entrypoints=entrypoints,
                           orphan_consumers=sorted(set(orphans)))
