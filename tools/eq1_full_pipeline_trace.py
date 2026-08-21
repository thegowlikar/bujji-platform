"""EQ1 Deliverable 2/3 — trace ONE complete order through the real
production_runtime pipeline, using REAL market data (2026-07-29
Bhavcopy, real strikes/premiums/expiry), not synthetic test fixtures.

Read-only with respect to Production: this script does not modify any
bujji/ source file. It only imports and calls real, existing, unmodified
engine code, exactly as the existing test suite does -- the only
difference from every existing test is that this run supplies REAL
spot_snapshot/option_chain data instead of None, which no existing
test does.
"""
import csv
import sys
from datetime import datetime

sys.path.insert(0, "/opt/bujji/app")

from bujji.production_runtime.composition_root import build_composition_root
from bujji.production_runtime.config import RuntimeConfig, RUNTIME_MODE_SHADOW
from bujji.production_runtime.runtime import PipelineInput, run_read_only, run_shadow
from bujji.trading_brain.nifty_contract_builder.models import (
    NiftySpotSnapshot, NiftyOptionChainSnapshot, NiftyOptionChainEntry,
)

FIXED_CLOCK = lambda: datetime(2026, 7, 30, 9, 20, 0)

# --- Step 1: real market evidence -- build real snapshots from the real
#     Bhavcopy already used in Live Shadow Session #3. ---
BHAVCOPY = "/opt/bujji/app/data/bhavcopy/BhavCopy_NSE_FO_0_0_0_20260729_F_0000.csv"

with open(BHAVCOPY) as f:
    reader = csv.DictReader(f)
    rows = [r for r in reader if r["TckrSymb"] == "NIFTY" and r["FinInstrmTp"] == "IDO" and r["StrkPric"]]

nearest_expiry = sorted(set(r["XpryDt"] for r in rows))[0]
real_spot = float(rows[0]["UndrlygPric"])
near = [r for r in rows if r["XpryDt"] == nearest_expiry and abs(float(r["StrkPric"]) - real_spot) <= 500]

entries = tuple(
    NiftyOptionChainEntry(
        strike=float(r["StrkPric"]),
        option_type=r["OptnTp"],
        expiry=nearest_expiry,
        contract_symbol=f"NSE:{r['FinInstrmNm']}",
    )
    for r in near
)
spot_snapshot = NiftySpotSnapshot(spot=real_spot, as_of="2026-07-30T09:20:00")
option_chain = NiftyOptionChainSnapshot(expiries=(nearest_expiry,), entries=entries, as_of="2026-07-30T09:20:00")

print(f"=== Real market evidence loaded ===")
print(f"Real spot: {real_spot} (from real Bhavcopy UndrlygPric, 2026-07-29)")
print(f"Real expiry: {nearest_expiry}")
print(f"Real option entries in chain: {len(entries)}")
print()

# --- Step 2: PipelineInput -- a real, valid MIC v2 classification
#     combination (MIC v2 runs in a separate process/venv, not directly
#     callable from here -- using the same valid combination the
#     existing test suite itself uses, disclosed honestly, not
#     presented as a live MIC v2 call). ---
pipeline_input = PipelineInput(
    market_context="TRENDING_UP",
    market_opinion="BULLISH",
    context_stability="STABLE",
    calibration="CALIBRATED",
    governance="APPROVED",
    lifecycle="ACTIVE",
    contract="COMPLETE",
)

# --- Step 3: build the real composition root -- SHADOW mode, paper broker. ---
root = build_composition_root(RuntimeConfig(mode=RUNTIME_MODE_SHADOW, broker_name="paper"))
print(f"=== CompositionRoot built === broker type: {type(root.broker).__name__}")
print()

# --- Step 4: Mode 1 -- read-only decision pipeline trace. ---
ro = run_read_only(root, pipeline_input, clock=FIXED_CLOCK)
print("=== Mode 1: run_read_only ===")
print(ro.trace)
print()

# --- Step 5: Mode 2 -- full shadow pipeline with REAL market data. ---
result = run_shadow(root, pipeline_input, spot_snapshot=spot_snapshot, option_chain=option_chain, clock=FIXED_CLOCK)
print("=== Mode 2: run_shadow (REAL spot/chain data, first time in this codebase's history) ===")
print(result.trace)
print()
print("--- Full stage-by-stage output ---")
print(f"EvidenceInterpretation: {result.evidence_interpretation.ontology_snapshot}")
print(f"MarketStateAssessment: state={result.market_state_assessment.market_state} confidence={result.market_state_assessment.confidence}")
print(f"StrategyDecision: selected={result.strategy_decision.selected_strategy} status={result.strategy_decision.selection_status}")
print(f"RiskAssessment: status={result.risk_assessment.status} approval={result.risk_assessment.approval}")
print(f"CapitalDecision: intent={result.capital_decision.capital_intent} status={result.capital_decision.allocation_status}")
print(f"ExecutionPlan: status={result.execution_plan.status}")
print(f"ExecutionInstructionSet: status={result.execution_instruction_set.status}, actions={result.execution_instruction_set.abstract_actions}")
print(f"BrokerExecutionRequest: {result.broker_execution_request}")
print(f"ContractConstructionResult: status={result.contract_construction_result.status}, contracts={len(result.contract_construction_result.contracts) if result.contract_construction_result.contracts else 0}")
if result.contract_construction_result.contracts:
    for c in result.contract_construction_result.contracts:
        print(f"    leg: {c}")
print(f"PositionPlan: validation={result.position_plan.validation if result.position_plan else None} lots_per_leg={result.position_plan.lots_per_leg if result.position_plan else None}")
print(f"OrderConstructionResult: status={result.order_construction_result.status if result.order_construction_result else None}, requests={len(result.order_construction_result.requests) if result.order_construction_result and result.order_construction_result.requests else 0}")
if result.order_construction_result and result.order_construction_result.requests:
    for req in result.order_construction_result.requests:
        print(f"    order: {req}")
print(f"ExecutionSession: state={result.execution_session.execution_state}")
print(f"RuntimeAuthorization: decision={result.runtime_authorization.decision} state={result.runtime_authorization.authorization_state}")
print(f"RuntimeSession: state={result.runtime_session.session_state}")
print(f"BrokerSession: auth={result.broker_session.authentication_state} session={result.broker_session.session_state}")
print(f"order_submitted: {result.order_submitted}")
print()

# --- Step 6: if dispatched, check the PaperBroker's own real ledger. ---
print(f"RuntimeAuthorization full detail: {result.runtime_authorization}")
print()

if result.order_submitted:
    import asyncio
    print("=== PaperBroker real state after dispatch ===")
    positions = asyncio.run(root.broker.get_open_positions())
    print(f"Open positions (real, in-memory): {positions}")
else:
    print("=== STOPPED before order_submitted=True. See trace above for exact stage. ===")
