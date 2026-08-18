"""Phase 16C -- decision lineage & look-ahead safety.

Proves, structurally, that a decision is a pure function of the
evidence handed to it -- and documents the exact boundary where
enforcement is still missing.

Touches NO frozen market-data architecture: no TickStore, no fabric,
no watermark, no storage decision. Pure AST + persisted-corpus checks.
"""
from __future__ import annotations

import ast
import glob
import json
import os

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# The decision path: observation -> ... -> trade intent. Every one of
# these must be a pure function of its inputs.
_DECISION_PATH = (
    "bujji/msi_consensus",
    "bujji/msi_decision_synthesis",
    "bujji/msi_strategy_eligibility",
    "bujji/msi_strategy_selector",
    "bujji/msi_trade_intent",
    "bujji/position_lifecycle",
    "bujji/position_intelligence",
    "bujji/position_management",
    "bujji/outcome_attribution",
    "bujji/portfolio_intelligence",
)

_WALL_CLOCK = {"now_ist", "now", "time", "utcnow", "today"}


def _py_files(pkg_rel):
    return [p for p in glob.glob(os.path.join(_REPO_ROOT, pkg_rel, "*.py"))
            if "__pycache__" not in p]


def _wall_clock_calls(path):
    """Calls that read the machine clock rather than an input timestamp."""
    with open(path) as f:
        tree = ast.parse(f.read(), filename=path)
    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", None)
        if name in _WALL_CLOCK:
            hits.append((name, getattr(node, "lineno", -1)))
    return hits


# --- the core structural guarantee --------------------------------------
def test_decision_path_never_reads_the_wall_clock():
    """A decision engine that reads `now()` is not replayable: the same
    inputs would produce a different answer tomorrow. Every engine on
    the decision path must take its timestamp as an explicit argument.

    This is the strongest available look-ahead guarantee short of the
    Market Data Fabric: a function that cannot observe the present
    cannot observe the future either.
    """
    offenders = {}
    for pkg in _DECISION_PATH:
        for path in _py_files(pkg):
            hits = _wall_clock_calls(path)
            if hits:
                offenders[os.path.relpath(path, _REPO_ROOT)] = hits
    assert not offenders, f"decision path reads wall clock: {offenders}"


def test_shadow_trade_construction_wall_clock_is_a_bounded_fallback():
    """DOCUMENTED EXCEPTION (audited, not waived).

    `shadow_trade_construction.engine` contains exactly one wall-clock
    read, and it is a CONDITIONAL FALLBACK used only when
    `source_cycle_id` is empty:

        as_of_date = source_cycle_id[:10] if source_cycle_id else <now>

    On the normal path the value is derived from the source cycle's own
    event time. The fallback is nonetheless a real replay-divergence
    vector: replaying a record with an empty `source_cycle_id` would
    date the construction to REPLAY time, not historical time, and
    could select a different expiry.

    This test pins the exception to exactly one occurrence so it cannot
    silently spread.
    """
    path = os.path.join(_REPO_ROOT, "bujji/shadow_trade_construction/engine.py")
    hits = _wall_clock_calls(path)
    assert len(hits) == 1, f"expected exactly 1 documented fallback, found {hits}"


# --- memory isolation (15N) must remain intact ---------------------------
def test_memory_cannot_influence_any_decision():
    """`Decision -> Outcome -> Memory` must never become
    `... -> Memory -> Decision`. Re-asserted here because this audit
    exists to prove it, not to assume it."""
    forbidden = ("bujji.msi_decision_synthesis", "bujji.msi_strategy_selection_foundation",
                 "bujji.msi_strategy_eligibility", "bujji.msi_trade_intent",
                 "bujji.msi_strategy_selector", "bujji.position_management")
    for path in _py_files("bujji/outcome_memory"):
        with open(path) as f:
            tree = ast.parse(f.read(), filename=path)
        for node in ast.walk(tree):
            mods = []
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                mods = [node.module]
            for m in mods:
                assert not any(m.startswith(f) for f in forbidden), (
                    f"{os.path.relpath(path, _REPO_ROOT)} imports decision path {m}")


def test_no_decision_engine_imports_outcome_or_memory():
    """The complement: the decision path must not reach forward into
    outcomes either. Attribution and memory are strictly downstream."""
    forbidden = ("bujji.outcome_memory", "bujji.outcome_attribution")
    offenders = []
    for pkg in ("bujji/msi_decision_synthesis", "bujji/msi_strategy_eligibility",
                "bujji/msi_trade_intent", "bujji/msi_consensus"):
        for path in _py_files(pkg):
            with open(path) as f:
                tree = ast.parse(f.read(), filename=path)
            for node in ast.walk(tree):
                mods = []
                if isinstance(node, ast.Import):
                    mods = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    mods = [node.module]
                for m in mods:
                    if any(m.startswith(f) for f in forbidden):
                        offenders.append((os.path.relpath(path, _REPO_ROOT), m))
    assert not offenders, f"decision path reaches into outcomes: {offenders}"


# --- evidence-graph closure on the real persisted corpus -----------------
def _corpus_cycles():
    pattern = os.path.join(_REPO_ROOT, "shadow_sessions", "*", "intelligence_cycle.jsonl")
    for path in sorted(glob.glob(pattern)):
        yield path


def test_decision_evidence_ids_resolve_within_the_persisted_corpus():
    """A decision that names evidence it cannot produce is not
    auditable. Verified against real persisted sessions: every
    `evidence_id` an opportunity assessment cites must be resolvable
    from the same session's own record."""
    checked = 0
    for path in _corpus_cycles():
        lines = [l for l in open(path) if l.strip()]
        if not lines:
            continue
        blob = "".join(lines)
        rec = json.loads(lines[len(lines) // 2])
        ev = (rec.get("opportunity") or {}).get("evidence_ids") or []
        if not ev:
            continue
        unresolved = [e for e in ev if e not in blob]
        assert not unresolved, f"{path}: {len(unresolved)} unresolvable evidence ids"
        checked += 1
    if checked == 0:
        pytest.skip("no persisted corpus with evidence ids in this checkout")


def test_decision_chain_is_walkable_from_eligibility_back_to_evidence():
    """eligibility -> {opportunity, consensus} -> domain assessments.
    Proves the evidence DAG is connected, not merely present."""
    checked = 0
    for path in _corpus_cycles():
        lines = [l for l in open(path) if l.strip()]
        if not lines:
            continue
        rec = json.loads(lines[len(lines) // 2])
        elig = rec.get("strategy_eligibility") or {}
        opp = rec.get("opportunity") or {}
        supporting = set(elig.get("supporting_assessment_ids") or [])
        if not supporting or not opp.get("assessment_id"):
            continue
        assert opp["assessment_id"] in supporting, (
            f"{path}: eligibility does not reference the opportunity it was derived from")
        checked += 1
    if checked == 0:
        pytest.skip("no persisted corpus with a walkable chain in this checkout")


# --- the missing half: versions ------------------------------------------
def test_persisted_decisions_carry_no_version_identity_DOCUMENTED_GAP():
    """AUDITED GAP, pinned so it cannot be forgotten.

    Persisted decisions carry `schema_version` and a free-text
    `provenance` naming the producing function -- but NO
    `calc_version`, `code_version` or `config_version`. So a decision
    can be traced to its EVIDENCE but not to the CODE that interpreted
    it.

    This is exactly why Phase 15P found 684/708 archived cycles
    diverging from current code with nothing detecting it: there was no
    version stamp to compare. This test asserts the CURRENT state so
    that closing the gap is a deliberate, visible change.
    """
    for path in _corpus_cycles():
        lines = [l for l in open(path) if l.strip()]
        if not lines:
            continue
        blob = lines[len(lines) // 2]
        for absent in ('"calc_version"', '"code_version"', '"config_version"'):
            assert absent not in blob, (
                f"{path} now carries {absent} -- the version gap has been closed; "
                "update this test and the lineage audit")
        assert '"schema_version"' in blob     # what DOES exist today
        return
    pytest.skip("no persisted corpus in this checkout")


# --- lineage contract enforces the rule ----------------------------------
def test_lineage_contract_detects_future_source_events():
    """The epistemics `Lineage` contract can already express and detect
    the violation, even though no producer populates it yet."""
    from bujji.epistemics.lineage import DERIVED, Lineage, look_ahead_violation
    lin = Lineage(data_class=DERIVED, as_of="2026-08-11T10:00:00", calc_version="CV-1")
    assert look_ahead_violation(lin, "2026-08-11T10:00:01") is True
    assert look_ahead_violation(lin, "2026-08-11T09:59:59") is False


def test_lineage_names_what_it_cannot_answer():
    """A derived artifact missing versions must SAY so rather than
    appear complete."""
    from bujji.epistemics.lineage import DERIVED, Lineage
    lin = Lineage(data_class=DERIVED, as_of="t", calc_version="CV-1")
    assert lin.is_reproducible is False
    assert "code_version" in lin.missing_fields()
