"""Safety tests -- Phase 16C canonical epistemics.

This package is PURE SEMANTICS. It must import nothing beyond the
standard library, must never fabricate a belief, and must never allow a
derived confidence to exceed its weakest critical input.
"""
from __future__ import annotations

import ast
import os

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MODULE_RELS = (
    "bujji/epistemics/uncertainty.py",
    "bujji/epistemics/lineage.py",
    "bujji/epistemics/adapters.py",
)
_ALLOWED_MODULES = {"__future__", "dataclasses", "typing", "hashlib",
                    "uncertainty", "lineage"}


def _abs(rel):
    return os.path.join(_REPO_ROOT, rel)


def _tree(rel):
    with open(_abs(rel)) as f:
        return ast.parse(f.read(), filename=rel)


def test_epistemics_is_pure_stdlib_only():
    """The strongest guarantee available: this package cannot depend on
    market data, brokers, storage or any Bujji subsystem, so it can be
    adopted anywhere without creating a dependency cycle."""
    for rel in _MODULE_RELS:
        for node in ast.walk(_tree(rel)):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module.split(".")[0]]
            for name in names:
                assert name in _ALLOWED_MODULES, f"{rel} imports {name!r}, outside stdlib+siblings"


def test_no_io_no_broker_no_execution():
    for rel in _MODULE_RELS:
        with open(_abs(rel)) as f:
            content = f.read()
        for forbidden in ("open(", "sqlite3", "requests", "socket", "place_order",
                          "fyers", "subprocess", "now_ist(", "datetime.now"):
            assert forbidden not in content, f"{rel} references {forbidden!r}"


def test_derived_confidence_never_exceeds_weakest_critical_input():
    """The central invariant, asserted exhaustively over every band
    combination rather than by example."""
    from bujji.epistemics.uncertainty import (
        ALL_CONFIDENCE, HIGH, KNOWN, Input, Uncertainty, compose, rank,
    )
    for a in ALL_CONFIDENCE:
        for b in ALL_CONFIDENCE:
            out = compose([
                Input("a", Uncertainty(state=KNOWN, confidence=a), critical=True),
                Input("b", Uncertainty(state=KNOWN, confidence=b), critical=True),
            ], base_confidence=HIGH)
            assert rank(out.confidence) <= min(rank(a), rank(b)), (a, b, out.confidence)


def test_composition_never_invents_a_value_from_nothing():
    """A critical UNKNOWN input can never yield a value-bearing output."""
    from bujji.epistemics.uncertainty import Input, compose, unknown
    out = compose([Input("only", unknown("no evidence"), critical=True)])
    assert out.carries_value is False
    assert out.confidence == "NONE"


def test_insufficient_history_is_never_silently_treated_as_unknown():
    """They mean different things: INSUFFICIENT_HISTORY self-heals.
    Collapsing them would teach a future learner that a temporary
    shortage is a permanent unknowable."""
    from bujji.epistemics.uncertainty import (
        INSUFFICIENT_HISTORY, UNKNOWN, Input, compose, insufficient,
    )
    out = compose([Input("ema200", insufficient("needs 200 bars"), critical=True)])
    assert out.state == INSUFFICIENT_HISTORY
    assert out.state != UNKNOWN


def test_stale_and_gap_are_never_actionable_regardless_of_confidence():
    """Risk and execution gate on `is_actionable`. A STALE value with
    nominally HIGH confidence must still be refused."""
    from bujji.epistemics.uncertainty import GAP, HIGH, STALE, Uncertainty
    assert Uncertainty(state=STALE, confidence=HIGH).is_actionable is False
    assert Uncertainty(state=GAP, confidence=HIGH).is_actionable is False


def test_limiting_factor_mandatory_whenever_belief_weakened():
    """A degraded confidence with no explanation is an unexplainable
    number, which defeats the purpose of the model."""
    from bujji.epistemics.uncertainty import (
        HIGH, KNOWN, LOW, Input, Uncertainty, compose, rank,
    )
    out = compose([Input("weak", Uncertainty(state=KNOWN, confidence=LOW), critical=True)],
                  base_confidence=HIGH)
    assert rank(out.confidence) < rank(HIGH)
    assert out.limiting_factor, "weakened belief must name what limited it"


def test_no_parallel_confidence_vocabulary_introduced():
    """This package must define exactly ONE confidence scale. If a
    future edit adds a second, this fails."""
    from bujji.epistemics import uncertainty as u
    assert u.ALL_CONFIDENCE == ("HIGH", "MODERATE", "LOW", "NONE")
    assert len(set(u.ALL_STATES)) == len(u.ALL_STATES)


def test_adapters_do_not_import_the_packages_they_adapt():
    """Adapters map VALUES, not objects -- so adopting the canonical
    model never creates a dependency on 15K/15M/15N/msi internals."""
    with open(_abs("bujji/epistemics/adapters.py")) as f:
        tree = ast.parse(f.read())
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert not node.module.startswith("bujji.position_lifecycle")
            assert not node.module.startswith("bujji.portfolio_intelligence")
            assert not node.module.startswith("bujji.outcome_memory")
            assert not node.module.startswith("bujji.msi_")
