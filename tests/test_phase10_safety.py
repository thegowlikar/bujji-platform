"""Safety tests -- Phase 10 Market Memory Integrity Upgrade.

Confirms the historical-event-hydration fix and memory_health
telemetry stay strictly observational/additive: no execution/risk/
capital/broker import anywhere in the changed files, and no strategy
threshold or decision logic was touched (only a data-plumbing fix in
market_state_builder + a new read-only telemetry module)."""
from __future__ import annotations

import ast
import os
import subprocess

# PaperBroker v2: the ONE authorized change inside the protected trading
# brain -- portfolio_risk_aggregator now distinguishes an empty book's
# genuinely-zero concentration from unknown data, which previously made
# the first trade of every fresh journal unplaceable (RISK_INVALID). Every
# fail-closed path for a NON-empty book is unchanged; see
# tests/test_portfolio_risk_empty_book.py. This guard still fails on any
# OTHER change under the protected packages.
_PAPERBROKER_V2_AUTHORIZED = ("bujji/trading_brain/risk_governor/portfolio_risk_aggregator.py",)


_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_PHASE10_CHANGED_FILES = (
    "bujji/market_state_builder/market_state.py",
    "bujji/market_state_builder/assessment_bridge.py",
    "bujji/market_state_builder/memory_health.py",
    "bujji/market_state/intelligence_cycle_recorder.py",
)

_FORBIDDEN_IMPORT_PREFIXES = (
    "bujji.execution_engine", "bujji.risk_governor", "bujji.trading_brain",
    "bujji.capital_brain", "bujji.msi_trade_construction", "bujji.msi_shadow_trading",
    "bujji.mic_replay", "bujji.production_runtime", "fyers_apiv3",
)

_FORBIDDEN_BROKER_CALL_PATTERN = (
    r"\.(place_order|modify_order|cancel_order|get_open_positions|get_positions|"
    r"get_margin|get_funds)\("
)


def test_no_forbidden_imports_in_phase10_changed_files():
    for rel_path in _PHASE10_CHANGED_FILES:
        path = os.path.join(_REPO_ROOT, rel_path)
        with open(path) as f:
            tree = ast.parse(f.read(), filename=path)
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                for forbidden in _FORBIDDEN_IMPORT_PREFIXES:
                    assert not name.startswith(forbidden), f"{rel_path} imports forbidden module {name}"


def test_no_forbidden_broker_calls_in_phase10_changed_files():
    for rel_path in _PHASE10_CHANGED_FILES:
        out = subprocess.run(
            ["grep", "-nE", _FORBIDDEN_BROKER_CALL_PATTERN, rel_path],
            cwd=_REPO_ROOT, capture_output=True, text=True,
        ).stdout.strip()
        assert out == "", f"forbidden broker call found in {rel_path}: {out}"


def test_no_strategy_or_threshold_packages_modified_this_phase():
    """Phase 10 must not touch any strategy-decision package -- only the
    data-plumbing layer (market_state_builder) and the recorder that
    persists its output."""
    result = subprocess.run(
        ["git", "diff", "--name-only", "360c003"],
        cwd=_REPO_ROOT, capture_output=True, text=True,
    )
    changed = [l for l in result.stdout.strip().splitlines() if l]
    changed = [l for l in changed if l not in _PAPERBROKER_V2_AUTHORIZED]
    forbidden_prefixes = (
        "bujji/msi_strategy_selector/", "bujji/msi_trade_intent/",
        "bujji/trading_brain/", "bujji/execution_engine/", "bujji/risk_governor/",
    )
    # msi_strategy_selection_foundation/ is EXCLUDED here -- it carries
    # its own separate, already-approved, already-tested Phase 9
    # exception (see test_intelligence_cycle_recorder_extended_safety.py).
    # Phase 10 itself does not touch it further.
    # Phase 14B: msi_trade_intent/engine.py carries its own additive,
    # already-approved exception (new function only, legacy untouched --
    # see docs/PHASE_14B_DECISION_PIPELINE_ARCHITECTURE.md and
    # tests/test_phase14b_safety.py).
    _phase14b_exception = ("bujji/msi_trade_intent/engine.py",)
    violations = [l for l in changed if any(l.startswith(p) for p in forbidden_prefixes) and l not in _phase14b_exception]
    assert violations == [], f"unexpected strategy-package changes in Phase 10: {violations}"


def test_memory_health_module_has_no_decision_shaped_fields():
    """MemoryHealth must stay pure observability -- no confidence/
    threshold/action field that could be mistaken for a decision input."""
    from bujji.market_state_builder.memory_health import MemoryHealth
    field_names = set(MemoryHealth.__dataclass_fields__.keys())
    forbidden = {"suitability", "action", "order", "strike", "quantity", "entry", "exit", "confidence_threshold"}
    assert not (field_names & forbidden), f"unexpected decision-shaped fields: {field_names & forbidden}"


def test_build_market_state_assessment_backward_compatible_without_event_history():
    """Every pre-Phase-10 caller that doesn't pass event_history must
    behave EXACTLY as before -- reproducing the original (buggy but
    unchanged-by-default) lookup scope, proving this is additive, not a
    silent behavior change for other callers."""
    from bujji.live_market_events.engine import detect_price_change
    from bujji.market_episode import engine as mee_engine
    from bujji.market_observation import engine as moc_engine, taxonomy as moc_taxonomy
    from bujji.market_state_builder.assessment_bridge import build_market_state_assessment

    def obs(ts, price):
        return moc_engine.build_observation(
            observation_type=moc_taxonomy.ALL_OBSERVATION_TYPES[0], instrument="NIFTY", exchange="NSE",
            segment="EQ", timestamp=ts, resolution=moc_taxonomy.RESOLUTION_ONE_MINUTE, source="TEST",
            schema_version="1.0.0", value_kind=moc_taxonomy.VALUE_KIND_SCALAR, payload=price,
            completeness=1.0, freshness=0.0, confidence=1.0, missing_fields=(),
            validation_status=moc_taxonomy.VALIDATION_VALID, source_quality="HIGH",
            originating_source="TEST", acquisition_timestamp=ts, normalization_timestamp=ts,
            origin=moc_taxonomy.ORIGIN_LIVE, provenance_version="1.0.0",
        )

    o1 = obs("2026-07-24T09:15:00", 100.0)
    o2 = obs("2026-07-24T09:16:00", 105.0)
    events = detect_price_change(o2, o1)
    episodes = mee_engine.process_event((), events[0], detection_context="TEST")

    # Old-style call: no event_history kwarg at all.
    result = build_market_state_assessment(episodes, events, (), "2026-07-24T09:16:00")
    assert result.events == events  # unchanged field semantics.
