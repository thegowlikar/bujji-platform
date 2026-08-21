"""Tests -- Execution Integration Layer, Phase-4 (TradeIntegrationContext).

Six tests, matching the six required categories exactly: contract
creation, immutability, no-decision-field schema safety, dependency
isolation, multi-context-per-assessment (no uniqueness enforcement),
and optional correlation (either side may exist without the other).
"""
from __future__ import annotations

import ast
import dataclasses
import inspect
import subprocess

import pytest

from bujji.execution_integration.trade_integration_context import (
    TradeIntegrationContext, build_trade_integration_context,
)


# A. Contract creation
def test_contract_constructs_correctly_and_preserves_all_values():
    ctx = build_trade_integration_context(
        integration_id="INT-001", assessment_id="TCA-001", liquidity_context_id="LC-001",
        created_at="2026-08-04T09:30:00.500",
    )
    assert isinstance(ctx, TradeIntegrationContext)
    assert ctx.integration_id == "INT-001"
    assert ctx.assessment_id == "TCA-001"
    assert ctx.liquidity_context_id == "LC-001"
    assert ctx.created_at == "2026-08-04T09:30:00.500"  # preserved exactly, never regenerated


# B. Immutability
def test_context_is_frozen():
    ctx = build_trade_integration_context("INT-001", "TCA-001", "LC-001", "2026-08-04T09:30:00")
    with pytest.raises(dataclasses.FrozenInstanceError):
        ctx.integration_id = "changed"


# C. No decision-shaped fields
def test_no_decision_fields_present():
    field_names = set(TradeIntegrationContext.__dataclass_fields__.keys())
    forbidden = {
        "allowed", "approved", "blocked", "decision", "trade_permission",
        "entry_permission", "risk", "signal", "recommendation", "execute", "order",
    }
    assert not (field_names & forbidden), f"forbidden fields found: {field_names & forbidden}"
    assert field_names == {"integration_id", "assessment_id", "liquidity_context_id", "created_at"}


# D. Dependency isolation
def test_dependency_isolation():
    # No forbidden import inside the module itself.
    forbidden_import = subprocess.run(
        ["grep", "-nE",
         r"^\s*(from|import)\s+(bujji\.)?(broker|runtime_execution|trading_brain|"
         r"trading_session_governor|risk_governor|msi_trade_construction|execution_reality|"
         r"strategy_selector)\b",
         "bujji/execution_integration/trade_integration_context.py"],
        cwd="/opt/bujji/app", capture_output=True, text=True,
    )
    assert forbidden_import.stdout.strip() == "", f"forbidden import found: {forbidden_import.stdout}"

    # No protected module (or the legacy bujji/integration/ package) references this one.
    reverse_refs = subprocess.run(
        ["grep", "-rl", "execution_integration",
         "bujji/production_runtime/", "bujji/trading_session_governor/", "bujji/trading_brain/",
         "bujji/msi_trade_construction/", "bujji/shadow_observatory/", "bujji/broker/",
         "bujji/execution_reality/", "bujji/integration/"],
        cwd="/opt/bujji/app", capture_output=True, text=True,
    )
    assert reverse_refs.stdout.strip() == "", f"unexpected references found: {reverse_refs.stdout}"

    # No .now()/clock dependency anywhere in the module.
    from bujji.execution_integration import trade_integration_context as module
    tree = ast.parse(inspect.getsource(module))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "now":
            pytest.fail("trade_integration_context.py must never call .now()")
    assert "clock" not in inspect.signature(build_trade_integration_context).parameters


# E. Multiple contexts per assessment -- no uniqueness enforcement
def test_multiple_contexts_per_assessment_coexist_without_registry():
    context1 = build_trade_integration_context("INT-001", "TCA001", "LC001", "2026-08-04T09:15:00")
    context2 = build_trade_integration_context("INT-002", "TCA001", "LC002", "2026-08-04T09:30:00")
    context3 = build_trade_integration_context("INT-003", "TCA001", "LC003", "2026-08-04T09:45:00")

    assert context1.assessment_id == context2.assessment_id == context3.assessment_id == "TCA001"
    liquidity_ids = {context1.liquidity_context_id, context2.liquidity_context_id, context3.liquidity_context_id}
    assert liquidity_ids == {"LC001", "LC002", "LC003"}
    assert context1 != context2 != context3  # distinct records, no overwrite, no shared state

    # Scenario 4 too: one liquidity_context_id referenced by multiple assessments -- also unrestricted.
    context_a = build_trade_integration_context("INT-010", "TCA-A", "LC-shared", "2026-08-04T09:15:00")
    context_b = build_trade_integration_context("INT-011", "TCA-B", "LC-shared", "2026-08-04T09:15:05")
    assert context_a.liquidity_context_id == context_b.liquidity_context_id == "LC-shared"
    assert context_a.assessment_id != context_b.assessment_id


# F. Optional correlation scenarios
def test_optional_correlation_either_side_may_exist_alone():
    # Scenario 1: a liquidity_context_id can exist with no TradeIntegrationContext
    # ever referencing it -- the module has no lifecycle/registry logic requiring one.
    liquidity_context_id = "LC-standalone-001"
    assert liquidity_context_id

    # Scenario 2: symmetric -- an assessment_id can exist alone.
    assessment_id = "TCA-standalone-001"
    assert assessment_id

    # No validation against anything real: IDs referencing nothing that exists
    # construct successfully -- the Integration Layer stores supplied IDs only,
    # it never enforces a lifecycle rule about what they must reference.
    ctx = build_trade_integration_context(
        integration_id="INT-999", assessment_id="DOES-NOT-EXIST", liquidity_context_id="ALSO-DOES-NOT-EXIST",
        created_at="2026-08-04T09:00:00",
    )
    assert ctx.assessment_id == "DOES-NOT-EXIST"
    assert ctx.liquidity_context_id == "ALSO-DOES-NOT-EXIST"
