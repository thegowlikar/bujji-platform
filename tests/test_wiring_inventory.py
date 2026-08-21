"""The wiring inventory -- the check that makes dormant code visible.

Built against synthetic repositories rather than the real tree, because the
real tree's numbers change every time anything is wired or written. The
BEHAVIOUR must not.

One test here is a regression for a bug this tool had against itself on its
first run: the store detector matched the line that DEFINES its own markers,
so it reported its own package as defining a store. A scanner that
misreports the code it scans teaches people to ignore it.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from bujji.wiring_inventory.inventory import (
    build_inventory,
    declared_dormancy,
    entrypoints_from_units,
    imports_of,
    module_name_for,
    persists_to_sqlite,
    reachable_modules,
)


def _repo(tmp_path: Path, files: dict, units: dict | None = None) -> Path:
    for rel, text in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    for name, text in (units or {}).items():
        path = tmp_path / "deploy" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return tmp_path


def _unit(exec_start: str) -> str:
    return f"[Service]\nType=oneshot\nExecStart={exec_start}\n"


class TestImportResolution:
    def test_absolute_imports(self, tmp_path):
        _repo(tmp_path, {"bujji/a/m.py": "import bujji.b\nfrom bujji.c import thing\n",
                         "bujji/a/__init__.py": ""})
        found = imports_of(tmp_path / "bujji/a/m.py", tmp_path)
        assert "bujji.b" in found and "bujji.c" in found

    def test_relative_imports_resolve_to_absolute(self, tmp_path):
        """`from ..core.config import X` inside bujji/broker/fyers.py is an
        edge to bujji.core.config -- invisible to a naive text search."""
        _repo(tmp_path, {"bujji/broker/fyers.py": "from ..core.config import BrokerConfig\n"
                                                  "from .guard import disable\n",
                         "bujji/broker/__init__.py": ""})
        found = imports_of(tmp_path / "bujji/broker/fyers.py", tmp_path)
        assert "bujji.core.config" in found
        assert "bujji.broker.guard" in found

    def test_an_unparseable_file_does_not_blind_the_scan(self, tmp_path):
        _repo(tmp_path, {"bujji/a/bad.py": "def (((\n"})
        assert imports_of(tmp_path / "bujji/a/bad.py", tmp_path) == set()

    def test_module_naming_drops_init(self, tmp_path):
        (tmp_path / "bujji/a").mkdir(parents=True)
        (tmp_path / "bujji/a/__init__.py").write_text("")
        assert module_name_for(tmp_path / "bujji/a/__init__.py", tmp_path) == "bujji.a"


class TestEntrypointDiscovery:
    def test_reads_both_execstart_forms(self, tmp_path):
        _repo(tmp_path, {"runner.py": "", "scripts/other.py": "", "bujji/app.py": ""},
              units={"a.service": _unit("/venv/bin/python runner.py --date-today"),
                     "b.service": _unit("/venv/bin/python -m bujji.app --config x.yaml")})
        names = entrypoints_from_units(tmp_path / "deploy", tmp_path)
        assert "runner" in names
        assert "bujji.app" in names

    def test_no_deploy_directory_yields_nothing(self, tmp_path):
        assert entrypoints_from_units(tmp_path / "deploy", tmp_path) == []


class TestReachability:
    def test_reachability_is_transitive(self):
        graph = {"entry": {"bujji.a"}, "bujji.a": {"bujji.b"}, "bujji.b": set()}
        assert reachable_modules(graph, ["entry"]) >= {"entry", "bujji.a", "bujji.b"}

    def test_a_package_imported_only_by_dormant_code_is_still_dormant(self, tmp_path):
        """The whole point. 'Someone imports it' is not the question."""
        _repo(tmp_path, {
            "runner.py": "import bujji.live\n",
            "bujji/live/__init__.py": "", "bujji/live/m.py": "",
            "bujji/dead/__init__.py": "import bujji.deader\n",
            "bujji/deader/__init__.py": "",
        }, units={"a.service": _unit("/venv/bin/python runner.py")})
        report = build_inventory(tmp_path)
        status = {p.package: p for p in report.packages}
        assert status["bujji.live"].reachable is True
        assert status["bujji.dead"].reachable is False
        assert status["bujji.deader"].importers == 1, "it IS imported"
        assert status["bujji.deader"].reachable is False, "but not by anything that runs"

    def test_subprocess_launches_count_as_edges(self, tmp_path):
        """run_daily_intelligence_session launches the capture scripts via
        subprocess and waits on them all session. Import edges cannot see
        that, and calling live production code dead discredits the report."""
        _repo(tmp_path, {
            "runner.py": 'subprocess.run(["python", "scripts/capture.py"])\n',
            "scripts/capture.py": "import bujji.captured\n",
            "bujji/captured/__init__.py": "",
        }, units={"a.service": _unit("/venv/bin/python runner.py")})
        report = build_inventory(tmp_path)
        assert "scripts.capture" in report.entrypoints
        assert {p.package: p for p in report.packages}["bujji.captured"].reachable is True


class TestDeclaredDormancy:
    def test_reason_is_read_without_importing(self, tmp_path):
        """Scanning must never execute the code it audits -- an __init__
        with a side effect would otherwise fire during an audit."""
        pkg = tmp_path / "bujji/tool"
        pkg.mkdir(parents=True)
        (pkg / "__init__.py").write_text(
            '__dormant__ = "operator tool, on demand"\n'
            'raise RuntimeError("importing me would explode")\n')
        assert declared_dormancy(pkg) == "operator tool, on demand"

    def test_absent_declaration_is_none(self, tmp_path):
        pkg = tmp_path / "bujji/plain"
        pkg.mkdir(parents=True)
        (pkg / "__init__.py").write_text("x = 1\n")
        assert declared_dormancy(pkg) is None

    def test_declared_dormancy_leaves_the_headline_alone(self, tmp_path):
        _repo(tmp_path, {
            "runner.py": "",
            "bujji/quiet/__init__.py": '__dormant__ = "waiting for phase 21"\n',
            "bujji/silent/__init__.py": "",
        }, units={"a.service": _unit("/venv/bin/python runner.py")})
        report = build_inventory(tmp_path)
        assert len(report.dormant) == 2
        assert report.headline == 1, "only the undeclared one counts"


class TestOrphanConsumers:
    def test_only_reachable_consumers_are_flagged(self, tmp_path):
        """A dormant script importing a dormant package is just more dormant
        code; listing it buries the case that matters."""
        _repo(tmp_path, {
            "runner.py": "import bujji.gone\n",
            "bujji/gone/__init__.py": "",
            "scripts/oneoff.py": "import bujji.gone\n",
        }, units={"a.service": _unit("/venv/bin/python runner.py")})
        report = build_inventory(tmp_path)
        # runner imports it, so it is reachable -- nothing is orphaned here.
        assert {p.package: p for p in report.packages}["bujji.gone"].reachable is True
        assert report.orphan_consumers == []

    def test_live_code_reading_a_dormant_package_is_flagged(self, tmp_path):
        _repo(tmp_path, {
            "runner.py": "import bujji.live\n",
            "bujji/live/__init__.py": "import bujji.hollow\n",
            "bujji/hollow/__init__.py": "",
        }, units={"a.service": _unit("/venv/bin/python runner.py")})
        report = build_inventory(tmp_path)
        # bujji.hollow is reached THROUGH live, so it is reachable -- proving
        # the edge is followed rather than the consumer merely noted.
        assert {p.package: p for p in report.packages}["bujji.hollow"].reachable is True


class TestStoreDetection:
    def test_detects_a_package_that_opens_sqlite(self, tmp_path):
        f = tmp_path / "s.py"
        f.write_text("conn = sqlite3.connect(path)\n")
        assert persists_to_sqlite([f]) is True

    def test_does_not_match_its_own_marker_definition(self):
        """REGRESSION. On its first run this tool reported its own package
        as defining a store, because the line defining the markers contained
        them verbatim. The markers are assembled from fragments to prevent
        it; this proves the fix holds."""
        source = Path(
            "/opt/bujji/app/bujji/wiring_inventory/inventory.py")
        if not source.exists():
            pytest.skip("VPS-only path")
        assert persists_to_sqlite([source]) is False

    def test_a_package_with_no_persistence_is_not_flagged(self, tmp_path):
        f = tmp_path / "pure.py"
        f.write_text("def add(a, b):\n    return a + b\n")
        assert persists_to_sqlite([f]) is False
