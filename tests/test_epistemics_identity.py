"""Phase 16D -- scientific reproducibility identity tests.

Proves the identity producers resolve REAL facts, never fabricate, and
reuse existing mechanisms rather than duplicating them.
"""
from __future__ import annotations

import ast
import os

import pytest

from bujji.epistemics.identity import (
    CalculationIdentity, CodeIdentity, ConfigIdentity, DecisionContext,
    ExperimentIdentity, RuntimeIdentity,
    resolve_calculation_identity, resolve_code_identity, resolve_config_identity,
    resolve_runtime_identity,
)

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# --- code identity -------------------------------------------------------
def test_code_identity_resolves_the_real_repository():
    ci = resolve_code_identity(_REPO_ROOT)
    assert ci.resolved is True
    assert ci.commit and len(ci.commit) == 40
    assert ci.branch
    assert isinstance(ci.dirty, bool)


def test_dirty_working_tree_is_recorded_not_hidden():
    """A result produced from an uncommitted tree is NOT reproducible
    from its commit. Bujji's tree has been uncommitted throughout this
    programme, so this must be visible rather than glossed over."""
    ci = resolve_code_identity(_REPO_ROOT)
    if ci.dirty:
        assert "+dirty" in ci.code_version
        assert ci.is_reproducible is False


def test_unresolvable_git_yields_none_never_a_placeholder(tmp_path):
    """A non-repo directory must produce None, not 'unknown' or ''."""
    ci = resolve_code_identity(str(tmp_path))
    assert ci.resolved is False
    assert ci.commit is None
    assert ci.code_version is None          # absence stays absence
    assert ci.is_reproducible is False


def test_dirty_none_is_not_treated_as_clean():
    """`dirty=None` means 'could not determine'. It must never be read
    as 'clean' -- that would silently upgrade an unknown to a guarantee."""
    ci = CodeIdentity(commit="a" * 40, short_commit="a" * 12, dirty=None, resolved=True)
    assert ci.is_reproducible is False


# --- config identity -----------------------------------------------------
def test_config_hash_is_deterministic_and_content_sensitive():
    a = resolve_config_identity({"poll": 30.0, "close": "15:20:00"})
    b = resolve_config_identity({"close": "15:20:00", "poll": 30.0})   # key order differs
    c = resolve_config_identity({"poll": 5.0, "close": "15:20:00"})
    assert a.config_hash == b.config_hash    # order-independent
    assert a.config_hash != c.config_hash    # content-sensitive


def test_absent_config_yields_unresolved_never_a_fabricated_hash():
    ci = resolve_config_identity(None, sources=("cfg.yaml",))
    assert ci.resolved is False
    assert ci.config_hash is None
    assert ci.is_reproducible is False


def test_multiple_config_versions_follow_the_existing_mil_next_pattern():
    ci = resolve_config_identity({"a": 1}, versions={"shadow": "1.0.0", "risk": "2.1.0"})
    assert dict(ci.versions) == {"shadow": "1.0.0", "risk": "2.1.0"}


# --- calculation identity ------------------------------------------------
def test_calc_version_is_content_derived_not_hand_maintained():
    a = resolve_calculation_identity("ema", "def ema(): ...", {"period": 20})
    assert a.calc_version == resolve_calculation_identity("ema", "def ema(): ...", {"period": 20}).calc_version
    assert a.calc_version != resolve_calculation_identity("ema", "def ema(): ...", {"period": 50}).calc_version
    assert a.calc_version != resolve_calculation_identity("ema", "def ema_v2(): ...", {"period": 20}).calc_version


def test_calc_version_records_the_parameters_used():
    ci = resolve_calculation_identity("bollinger", "def bb(): ...", {"period": 20, "num_std": 2.0})
    assert dict(ci.parameters) == {"period": "20", "num_std": "2.0"}


# --- runtime bundle ------------------------------------------------------
def test_runtime_identity_names_why_it_is_not_reproducible():
    rt = resolve_runtime_identity(config_payload=None, repo_root=_REPO_ROOT)
    assert rt.is_fully_reproducible is False
    assert "config_identity_unresolved" in rt.unreproducible_reasons()


def test_fully_reproducible_requires_clean_code_and_resolved_config():
    clean = CodeIdentity(commit="a" * 40, short_commit="a" * 12, dirty=False, resolved=True)
    cfg = ConfigIdentity(config_hash="h", resolved=True)
    assert RuntimeIdentity(code=clean, config=cfg).is_fully_reproducible is True
    dirty = CodeIdentity(commit="a" * 40, short_commit="a" * 12, dirty=True, resolved=True)
    rt = RuntimeIdentity(code=dirty, config=cfg)
    assert rt.is_fully_reproducible is False
    assert "working_tree_dirty" in rt.unreproducible_reasons()


# --- ADAPTER onto the existing SessionManifest ---------------------------
def test_adapts_onto_existing_session_manifest_without_replacing_it():
    """`SessionManifest` already declares `code_version`/`config_hash`
    and refuses to fabricate them. This proves the producer supplies
    exactly those fields -- reuse, not replacement."""
    from bujji.shadow_observatory.models import SessionManifest

    rt = resolve_runtime_identity(config_payload={"x": 1}, repo_root=_REPO_ROOT)
    fields = rt.to_session_manifest_fields()
    assert set(fields) == {"code_version", "config_hash"}

    manifest = SessionManifest(
        session_id="S1", start_time="t", mode="shadow", strategy_engine="msi",
        risk_engine="none", broker="paper", market="NIFTY", symbols=(), **fields,
    )
    assert manifest.code_version == rt.code.code_version
    assert manifest.config_hash == rt.config.config_hash


def test_unresolved_identity_leaves_manifest_fields_none():
    """`SessionManifest`'s own rule: a missing value stays None."""
    rt = RuntimeIdentity(code=CodeIdentity(), config=ConfigIdentity())
    assert rt.to_session_manifest_fields() == {"code_version": None, "config_hash": None}


# --- decision context: removes wall-clock dependence ----------------------
def test_decision_context_supplies_as_of_date_without_a_clock():
    """Replaces the audited `datetime.now()` fallback: the date comes
    from the injected event time, so a replayed record is dated by the
    RECORD, never by replay time."""
    ctx = DecisionContext(as_of="2026-08-06T09:14:51+00:00", session_id="S1")
    assert ctx.as_of_date == "2026-08-06"


def test_decision_context_is_replay_stable():
    """Same context, same answer -- regardless of when it is evaluated."""
    ctx = DecisionContext(as_of="2026-08-06T09:14:51+00:00")
    assert ctx.as_of_date == ctx.as_of_date == "2026-08-06"


# --- experiment identity (contract only) ---------------------------------
def test_production_artifacts_carry_no_experiment_identity():
    """Absence is meaningful: it distinguishes a live decision from a
    research artifact."""
    assert ExperimentIdentity().is_research is False


def test_any_experiment_field_marks_the_artifact_as_research():
    for kw in ("run_id", "experiment_id", "campaign_id"):
        assert ExperimentIdentity(**{kw: "x"}).is_research is True


# --- reuse proofs --------------------------------------------------------
def test_reuses_replay_engine_fingerprint_rather_than_adding_a_seventh():
    """Six fingerprint implementations already exist. Identity must use
    the canonical replay owner's, not add another."""
    path = os.path.join(_REPO_ROOT, "bujji/epistemics/identity.py")
    with open(path) as f:
        src = f.read()
    assert "from bujji.replay_engine.engine import fingerprint_state" in src
    assert "hashlib" not in src, "identity.py must not implement its own hashing"


def test_identity_impurity_is_bounded_to_git_inspection():
    """`identity.py` is the ONE impure module in `epistemics`. Its
    impurity must be exactly git inspection -- no network, no broker,
    no market data, no decision logic."""
    path = os.path.join(_REPO_ROOT, "bujji/epistemics/identity.py")
    with open(path) as f:
        tree = ast.parse(f.read())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    allowed = {"__future__", "os", "subprocess", "dataclasses", "typing",
               "bujji.replay_engine.engine"}
    assert imported <= allowed, f"identity.py imports beyond its bounded edge: {imported - allowed}"


def test_pure_epistemics_modules_stay_pure():
    """Adding the impure edge must not contaminate the pure core."""
    for rel in ("bujji/epistemics/uncertainty.py", "bujji/epistemics/lineage.py",
                "bujji/epistemics/adapters.py"):
        with open(os.path.join(_REPO_ROOT, rel)) as f:
            src = f.read()
        assert "subprocess" not in src, f"{rel} lost its purity"
        assert "import os" not in src, f"{rel} lost its purity"


def test_identity_never_touches_broker_or_market_data():
    path = os.path.join(_REPO_ROOT, "bujji/epistemics/identity.py")
    with open(path) as f:
        src = f.read()
    for forbidden in ("fyers", "place_order", "TickStore", "socket", "requests", "sqlite3"):
        assert forbidden not in src, f"identity.py references {forbidden!r}"
