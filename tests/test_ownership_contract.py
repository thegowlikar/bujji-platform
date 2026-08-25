"""The ownership document must agree with measured reachability.

WHY THIS TEST EXISTS. `docs/SYSTEM_OWNERSHIP.md` carries a "Legacy ORB-VWAP
ownership" table and a "Shared infrastructure -- DO NOT DELETE" table. The
legacy table was read as a delete-list, and five files in it are reachable from
enabled entrypoints -- including `bujji/execution/engine.py`, reachable from
the unit that trades. Deleting it during an ORB-VWAP cleanup would break
production.

Ownership is not the same as harmlessness. This test holds the distinction:
a file may be listed under the generation that designed it, but if production
can reach it, the table must say so.
"""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
DOC = REPO / "docs" / "SYSTEM_OWNERSHIP.md"


def _reachability():
    spec = importlib.util.spec_from_file_location(
        "ownership_reachability", REPO / "tools" / "reachability.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["ownership_reachability"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def closure():
    m = _reachability()
    mods = m.module_map(REPO)
    graph = {mod: m.imports_of(path, mod, path.name == "__init__.py")
             for mod, path in mods.items()}
    return m.reachable_from(m.ENTRY_POINTS, graph)


HEADING = "## Legacy ORB-VWAP ownership"


def _legacy_table():
    """Extract the Legacy ORB-VWAP ownership table.

    A plain function, not a fixture: an assertion inside a fixture is reported
    by pytest as an ERROR, and an errored control cannot distinguish "the guard
    caught something" from "the harness broke". Called from the test body, the
    same assertion is a clean failure.
    """
    text = DOC.read_text(encoding="utf-8")
    assert HEADING in text, (
        f"{DOC.name} no longer contains {HEADING!r}; this contract cannot be "
        f"checked. If the table was renamed, update this test deliberately.")
    begin = text.index(HEADING)
    return text[begin:text.index("## ", begin + 10)]


def _paths_in(block: str):
    return set(re.findall(r"`(bujji/[A-Za-z0-9_/]+\.py)", block))


def _module_of(path: str) -> str:
    return path[:-3].replace("/", ".")


def test_the_anchor_holds():
    """Guards the parser: if the table stops being found, everything below
    would pass vacuously on an empty set."""
    paths = _paths_in(_legacy_table())
    assert len(paths) >= 10, f"legacy table parsed as only {len(paths)} files"
    assert "bujji/app.py" in paths


def test_reachable_legacy_files_are_marked_shared(closure):
    """Every reachable file in the legacy table must carry the SHARED mark."""
    unmarked = []
    for line in _legacy_table().splitlines():
        if not line.startswith("|") or "SHARED" in line:
            continue
        for path in _paths_in(line):
            if _module_of(path) in closure:
                unmarked.append(path)
    assert not unmarked, (
        "these legacy-table files are reachable from an enabled entrypoint but "
        "are not marked (SHARED); a reader treating this table as a delete-list "
        f"would break production: {sorted(set(unmarked))}")


def test_files_marked_shared_really_are_reachable(closure):
    """The mark must not be applied defensively to files nothing reaches.

    A mark that is always safe to add carries no information.
    """
    wrong = []
    for line in _legacy_table().splitlines():
        if not line.startswith("|") or "SHARED" not in line:
            continue
        for path in _paths_in(line):
            if _module_of(path) not in closure:
                wrong.append(path)
    assert not wrong, (
        f"marked (SHARED) but not reachable from any enabled entrypoint: {wrong}")


def test_the_five_known_shared_modules_are_still_reachable(closure):
    """Named explicitly so a silent loss of reachability is visible."""
    for mod in ("bujji.core.config", "bujji.core.thesis",
                "bujji.execution.engine", "bujji.broker.factory",
                "bujji.broker.fyers_ws"):
        assert mod in closure, f"{mod} is no longer reachable; update the doc"


def test_entry_point_count_in_architecture_doc():
    """The prose figure drifted once: the document said eight, the tool
    declared seven, and the figures-block test did not cover prose."""
    m = _reachability()
    words = {7: "seven", 8: "eight", 9: "nine", 6: "six"}
    n = len(m.ENTRY_POINTS)
    text = (REPO / "ARCHITECTURE.md").read_text(encoding="utf-8")
    assert f"the {words[n]} entry points systemd actually" in text, (
        f"ARCHITECTURE.md does not say '{words[n]} entry points' but "
        f"ENTRY_POINTS has {n}")
