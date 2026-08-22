"""ARCHITECTURE.md is enforced, not decorative.

WHY. This repository already had TWO architecture documents, each declaring
itself authoritative, both ~4 weeks stale across a period in which the entry
path changed in eight places. A document nothing checks decays into folklore,
and folklore about which module owns a truth is how two parts of a trading
system come to disagree about what is held.

So the ownership table is machine-read and machine-enforced:

  * a type declared OWNED must have EXACTLY ONE definition on the reachable
    path, in the module named;
  * a type declared CONTESTED must STILL be contested -- resolving one and
    leaving the table stale fails, which is what keeps the document honest as
    milestones land;
  * the reachability figures quoted in the document must match what the tool
    reports today.

`ARCHITECTURE.md` at the repo root is canonical. `docs/ARCHITECTURE.md` is
deprecated; a test below pins that it says so.
"""
from __future__ import annotations

import ast
import importlib.util
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
ARCH = REPO_ROOT / "ARCHITECTURE.md"
REGEN = "python tools/reachability.py"


def _load_tool():
    spec = importlib.util.spec_from_file_location(
        "bujji_reachability", REPO_ROOT / "tools" / "reachability.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def report():
    return _load_tool().analyse(REPO_ROOT)


@pytest.fixture(scope="module")
def definitions(report):
    """class name -> the reachable production modules that define it."""
    tool = _load_tool()
    mods = tool.module_map(REPO_ROOT)
    out: dict[str, list[str]] = {}
    for name in report["reachable_modules"]:
        try:
            tree = ast.parse(mods[name].read_text(encoding="utf-8", errors="replace"))
        except Exception:  # noqa: BLE001
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                out.setdefault(node.name, []).append(name)
    return out


_ROW = re.compile(r"^\|\s*[^|]+\|\s*`([A-Za-z_][\w]*)`\s*\|\s*`([\w.]+)`\s*\|\s*(OWNED|CONTESTED)\s*\|\s*$")


def _table():
    rows = []
    for line in ARCH.read_text(encoding="utf-8").splitlines():
        m = _ROW.match(line.strip())
        if m:
            rows.append((m.group(1), m.group(2), m.group(3)))
    return rows


def test_the_ownership_table_is_parseable_and_populated():
    rows = _table()
    assert len(rows) >= 15, f"only {len(rows)} ownership rows parsed -- the table format changed"
    assert any(s == "OWNED" for _, _, s in rows)
    assert any(s == "CONTESTED" for _, _, s in rows)


def test_every_owned_type_has_exactly_one_reachable_definition(definitions):
    """The contract itself. A second definition of an owned type is how two
    parts of the system come to disagree about what is true."""
    problems = []
    for typ, owner, status in _table():
        if status != "OWNED":
            continue
        where = definitions.get(typ, [])
        if len(where) != 1:
            problems.append(f"{typ}: declared OWNED by {owner}, found {len(where)} "
                            f"reachable definitions {where}")
        elif where[0] != owner:
            problems.append(f"{typ}: declared OWNED by {owner}, actually defined in {where[0]}")
    assert not problems, "ownership violations:\n  " + "\n  ".join(problems)


def test_contested_types_are_still_contested(definitions):
    """The ratchet. Resolving a conflict and leaving the table saying CONTESTED
    would let the document rot in the safe-looking direction -- and the entry
    would never be promoted to OWNED, so nothing would ever enforce it."""
    stale = []
    for typ, _owner, status in _table():
        if status != "CONTESTED":
            continue
        where = definitions.get(typ, [])
        if len(where) <= 1:
            stale.append(f"{typ}: table says CONTESTED but only {len(where)} definition(s) "
                         f"remain {where} -- promote it to OWNED")
    assert not stale, "stale CONTESTED entries:\n  " + "\n  ".join(stale)


def test_the_reachability_figures_in_the_document_are_current(report):
    """A living document, enforced. These numbers are the reason to believe
    anything else in the file."""
    text = ARCH.read_text(encoding="utf-8")
    t = report["totals"]
    expected = {
        "python files": t["python_files"],
        "test modules": t["test_modules"],
        "production modules": t["production_modules"],
        "REACHABLE": t["reachable"],
        "orphaned": t["orphaned"],
        "test-only": t["test_only"],
        "unreferenced": t["unreferenced"],
    }
    wrong = []
    for label, value in expected.items():
        if not re.search(rf"^\s*{re.escape(label)}\s+{value}\b", text, re.M):
            wrong.append(f"{label} should read {value}")
    assert not wrong, (
        "ARCHITECTURE.md's reachability block is stale:\n  "
        + "\n  ".join(wrong)
        + f"\nRegenerate with: {REGEN}")


def test_the_percentage_quoted_is_consistent_with_the_counts(report):
    t = report["totals"]
    pct = 100.0 * t["reachable"] / t["production_modules"]
    assert re.search(rf"\({pct:.1f}% of production\)", ARCH.read_text(encoding="utf-8")), \
        f"the quoted percentage should be {pct:.1f}%"


def test_the_deprecated_document_says_so_in_its_own_first_lines():
    """Two documents each claiming authority is what this replaces. The
    deprecation must be visible to someone who opens the OLD file, not only to
    someone who opens the new one."""
    old = (REPO_ROOT / "docs" / "ARCHITECTURE.md").read_text(encoding="utf-8")
    head = "\n".join(old.splitlines()[:12]).upper()
    assert "DEPRECATED" in head, "docs/ARCHITECTURE.md does not announce its own deprecation"
    assert "ARCHITECTURE.MD" in head


def test_the_safety_contract_is_present_and_numbered():
    """The six rules are the part of this document other code is judged against."""
    text = ARCH.read_text(encoding="utf-8")
    assert "## 1. Safety contract" in text
    for n in range(1, 7):
        assert re.search(rf"^{n}\.\s+\*\*", text, re.M), f"safety rule {n} is missing"


def test_test_only_is_defined_as_a_classification_not_a_verdict():
    """The operator's instruction, pinned: a test-only module cannot support a
    production-safety claim. That is NOT a licence to delete it."""
    text = ARCH.read_text(encoding="utf-8")
    assert "classification, not a verdict" in text
    assert "cannot support a claim about production safety" in text
    tool = (REPO_ROOT / "tools" / "reachability.py").read_text(encoding="utf-8")
    assert "never recommends deleting" in tool


# --------------------------------------------------------------- quarantine
_QUARANTINE_ROW = re.compile(r"^\|\s*`([\w.]+)`\s*\|\s*([a-z, -]+?)\s*\|\s*QUARANTINED\s*\|\s*$")
_VIOLATION_ROW = re.compile(r"^\|\s*`([\w.]+)`\s*\|\s*([a-z-]+)\s*\|\s*(M\d)\s*\|\s*$")


def _quarantined():
    return {m.group(1): [p.strip() for p in m.group(2).split(",")]
            for m in (_QUARANTINE_ROW.match(l.strip()) for l in ARCH.read_text(encoding="utf-8").splitlines())
            if m}


def _declared_violations():
    out = {}
    for line in ARCH.read_text(encoding="utf-8").splitlines():
        m = _VIOLATION_ROW.match(line.strip())
        if m:
            out.setdefault(m.group(1), set()).add(m.group(2))
    return out


@pytest.fixture(scope="module")
def patterns():
    spec = importlib.util.spec_from_file_location(
        "bujji_forbidden", REPO_ROOT / "tools" / "forbidden_patterns.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_detector_finds_a_known_instance(patterns):
    """POSITIVE CONTROL. A detector that finds nothing would pass every test
    below while proving nothing at all."""
    known = REPO_ROOT / "bujji" / "trading_brain" / "risk_governor" / "msi_entry_bridge.py"
    hits = patterns.scan_source(patterns.executable_source(known))
    assert any(k == "broker-symbol-built" for k, _ in hits), \
        "the detector no longer finds the symbol construction in msi_entry_bridge"


def test_the_detector_ignores_prose(patterns):
    """The reason docstrings are stripped: this repository's own explanations
    of why `expiryData[0]` is forbidden must not read as committing it."""
    code = 'def f():\n    """We used to read expiryData[0] and strike_count=5."""\n    return 1\n'
    import ast as _ast, tempfile, os
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as fh:
        fh.write(code); path = Path(fh.name)
    try:
        assert patterns.scan_source(patterns.executable_source(path)) == []
    finally:
        os.unlink(path)


def test_no_quarantined_module_is_reachable_from_production(report, patterns):
    """THE RATCHET. A dormant forbidden pattern must not re-enter the runtime
    silently. Wiring one of these into a systemd path fails here until it is
    migrated or explicitly removed from quarantine."""
    reachable = set(report["reachable_modules"])
    breached = sorted(set(_quarantined()) & reachable)
    assert not breached, (
        "quarantined modules are now reachable from production:\n  "
        + "\n  ".join(breached)
        + "\nEither remove the forbidden pattern, or take the module out of "
          "quarantine in ARCHITECTURE.md with the reason.")


def test_quarantined_modules_still_exist_and_still_carry_their_pattern(patterns):
    """A quarantine entry for a module that no longer has the pattern is
    stale, and stale fences teach people to ignore fences."""
    stale = []
    tool = _load_tool()
    mods = tool.module_map(REPO_ROOT)
    for module, declared in _quarantined().items():
        if module not in mods:
            stale.append(f"{module}: no such module")
            continue
        found = {k for k, _ in patterns.scan_source(patterns.executable_source(mods[module]))}
        missing = set(declared) - found
        if missing:
            stale.append(f"{module}: no longer carries {sorted(missing)} -- remove the entry")
    assert not stale, "stale quarantine entries:\n  " + "\n  ".join(stale)


def test_reachable_violations_match_the_declaration_exactly(report, patterns):
    """THE RATCHET, BOTH WAYS. Undeclared violations fail because they are new.
    Declared violations that no longer exist also fail, so the table cannot rot
    in the safe-looking direction."""
    reachable = set(report["reachable_modules"])
    actual = {}
    for module, hits in patterns.scan_repo(REPO_ROOT).items():
        if module in reachable:
            actual[module] = {k for k, _ in hits}
    declared = _declared_violations()

    undeclared = {m: sorted(v) for m, v in actual.items() if m not in declared}
    resolved = {m: sorted(v) for m, v in declared.items() if m not in actual}
    changed = {m: (sorted(declared[m]), sorted(actual[m]))
               for m in set(declared) & set(actual) if declared[m] != actual[m]}

    assert not undeclared, (
        "NEW reachable forbidden patterns, undeclared:\n  "
        + "\n  ".join(f"{m}: {v}" for m, v in undeclared.items()))
    assert not resolved, (
        "declared reachable violations that no longer exist -- remove them:\n  "
        + "\n  ".join(f"{m}: {v}" for m, v in resolved.items()))
    assert not changed, (
        "declared patterns differ from what is present:\n  "
        + "\n  ".join(f"{m}: declared {d}, found {a}" for m, (d, a) in changed.items()))


def test_every_declared_violation_names_a_milestone():
    text = ARCH.read_text(encoding="utf-8")
    assert _declared_violations(), "no reachable violations declared -- the table format changed"
    for module in _declared_violations():
        assert re.search(rf"`{re.escape(module)}`\s*\|[^|]+\|\s*M\d\s*\|", text), \
            f"{module} is declared without a clearing milestone"
