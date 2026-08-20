"""Safety verification -- Intelligence Cycle Recorder extension
(Strategy Suitability/Selection/Trade Intent), 2026-08-06.

Confirms the recorder still imports only description-producing MSI
engines -- never Trade Construction, Risk Governor, Execution, or any
broker order/position/margin method -- and that all newly-called
engines remain unmodified.
"""
from __future__ import annotations

import subprocess

# PaperBroker v2: the ONE authorized change inside the protected trading
# brain -- portfolio_risk_aggregator now distinguishes an empty book's
# genuinely-zero concentration from unknown data, which previously made
# the first trade of every fresh journal unplaceable (RISK_INVALID). Every
# fail-closed path for a NON-empty book is unchanged; see
# tests/test_portfolio_risk_empty_book.py. This guard still fails on any
# OTHER change under the protected packages.
_PAPERBROKER_V2_AUTHORIZED = ("bujji/trading_brain/risk_governor/portfolio_risk_aggregator.py",)

# Lot-size-authoritative fix (2026-08-18): the ONE authorized change to the
# production_runtime lineage since the 360c003 baseline. The 2026-07-19 audit
# (bujji/broker/instrument_master.py module docstring) found the live symbol
# master says NIFTY lot=65 while RuntimeConfig defaulted to 75 and the
# composition root sized from that default. config.py demotes lot_size to an
# optional cross-check; composition_root.py resolves the authoritative value
# from the instrument master and fails closed (CompositionError) when it
# cannot. Covered by tests/test_lot_size_from_master.py. No decision,
# strategy, or execution semantics changed.
_LOT_SIZE_AUTHORITATIVE_AUTHORIZED = (
    "bujji/production_runtime/config.py",
    "bujji/production_runtime/composition_root.py",
)

# CP-C safety spine (2026-08-19, Master Plan D-6): the ONE further authorized
# change to the production_runtime lineage since baseline 360c003. Gate B --
# capital_check.assess_capital, documented as the sole margin veto authority
# but invoked nowhere -- is now wired into process_entry_cycle with the
# proposal's real legs priced through the certified SPAN provider. Covered by
# the GateB tests in tests/test_trading_brain_runtime.py. No strategy,
# construction, or execution semantics changed; a new VETO gate was added.
_CPC_SAFETY_SPINE_AUTHORIZED = (
    "bujji/production_runtime/trading_brain_runtime.py",
    "bujji/production_runtime/trading_brain_composition_root.py",
)


RECORDER_FILE = "bujji/market_state/intelligence_cycle_recorder.py"

FORBIDDEN_IMPORTS = (
    r"^\s*(from|import)\s+(bujji\.)?(trading_brain|execution_engine|risk_governor|"
    r"msi_trade_construction|msi_shadow_trading|execution_integration|"
    r"broker\b|mic_replay|mic_v2)\b"
)


def _grep(pattern, path, flags="-rnE"):
    return subprocess.run(
        ["grep", flags, pattern, path], cwd="/opt/bujji/app", capture_output=True, text=True,
    ).stdout.strip()


# CP-C evidence + execution realism (D-5/D-7/D-8, 2026-08-19). THREE NEW
# FILES under a protected prefix, authorized by name rather than by
# advancing the baseline -- advancing it would blanket-approve every diff
# since 360c003, including ones nobody reviewed. Each is ADDITIVE: a new
# module with no existing caller changed, no protected decision module
# touched, no risk rule's meaning altered.
#   paper_market_sync    -- pushes REAL observed bid/ask into PaperBroker so
#                           fills stop being frictionless (D-7).
#   execution_costs      -- sums the broker's OWN reported charges/slippage
#                           so an outcome is net, not gross (D-8).
#   outcome_memory_writer-- appends the OutcomeMemoryRecord to the SAME
#                           EventStore outcome_memory.recovery already
#                           replays, so a trade survives the process (D-8).
_CPC_EVIDENCE_AND_REALISM_AUTHORIZED = (
    "bujji/production_runtime/paper_market_sync.py",
    "bujji/production_runtime/execution_costs.py",
    "bujji/production_runtime/outcome_memory_writer.py",
)


# AUTHORIZED CHANGE (2026-08-19, operator-approved): live-premium fix in
# bujji/msi_trade_construction/engine.py. The engine read `row.settlement`
# as the ONLY premium source. The bhavcopy replay provider populates
# settlement; the LIVE chain provider explicitly does not (settlement=None,
# traded price in `close`). On live data every strike therefore resolved to
# premium=None -> iv=None -> delta=None -> ZERO candidates, and every live
# entry attempt died with REJECT_STRIKE_UNAVAILABLE. Bujji could not
# construct a trade on live data at all -- a live-only failure invisible to
# this suite, which drives the bhavcopy path exclusively.
#
# The fix is `_premium_for(row)`: settlement FIRST (every bhavcopy decision,
# replay and test bit-for-bit unchanged -- agreement asserted by
# tests/test_live_chain_strike_selection.py), then the mid of a real
# two-sided quote, then the last trade; absence stays absence, and the basis
# used is recorded on the evidence. Selection logic, target deltas and
# rejection semantics are untouched.
_LIVE_PREMIUM_FIX_AUTHORIZED = ("bujji/msi_trade_construction/engine.py",)

# AUTHORIZED CHANGE (2026-08-19, operator directive): three-part regime
# strategy selection. The previous table had two tradeable outcomes, both
# neutral (IRON_CONDOR / IRON_FLY), so a TRENDING market always resolved to
# no-trade -- its own reasoning blamed the absence of "a Gate-B-approved
# defined-risk SELLING strategy", which was true only because no directional
# credit spread existed in the construction engine.
#
#   bujji/msi_trade_construction/engine.py    + BULL_PUT_SPREAD / BEAR_CALL_SPREAD,
#     built from the SAME helpers IRON_CONDOR uses (_nearest_by_delta,
#     _wing_width, _nearest_grid). No existing family's branch is touched.
#   bujji/msi_trade_construction/taxonomy.py  + the two families in
#     SUPPORTED_FAMILIES and DEFINED_RISK_FAMILIES (every short leg is paired
#     with a protective long -- a structural fact, not a P&L claim).
#   bujji/msi_trade_construction/config.py    + delta targets (0.20, the same
#     premium-selling distance the other selling families use).
#   .../trading_session_governor/strategy_selector.py  three-part rewrite.
#
# RISK POSTURE CHANGED, with explicit operator approval: the sideways branch
# now selects short straddle / short strangle, which carry NAKED short legs
# and appear in taxonomy.UNDEFINED_RISK_FAMILIES. Every previously selectable
# family was defined-risk. Gate B's real SPAN veto, the capital check,
# portfolio limits, the daily loss limit, the emergency brake and the
# mandatory exit all still apply; the SHAPE no longer bounds the loss.
#
# Every no-trade path is unchanged: unknown regime, volatility expansion
# (checked before direction, so a directional branch cannot reach around it)
# and any unmapped combination still fail closed. Coverage:
# tests/test_three_part_strategy_selection.py.
_THREE_PART_SELECTION_AUTHORIZED = (
    "bujji/msi_trade_construction/engine.py",
    "bujji/msi_trade_construction/taxonomy.py",
    "bujji/msi_trade_construction/config.py",
    "bujji/production_runtime/trading_session_governor/strategy_selector.py",
)

# AUTHORIZED CHANGE (2026-08-20, operator decision): DEFINED-RISK MODE for
# real money. Measured over 168,194 real 5-minute NIFTY bars: the stop-loss,
# daily loss limit and emergency brake all run on ONE 300-second management
# heartbeat, and the worst single bar in nine years ranged 611.8 points --
# ~Rs 39,764 of adverse move against a real 240.95-point straddle credit
# inside one unchecked interval, 2.5x the stop. A naked shape has no
# structural floor; a wing holds regardless of loop cadence.
#
#   .../trading_session_governor/session_governor.py  threads defined_risk_only
#     into select_strategy. Additive keyword, default False; the RUNNER owns
#     the fail-closed derivation (anything not literally `shadow_mode: true`
#     selects defined-risk only). No existing behaviour changes in paper mode.
#
# The selector itself is already covered by _THREE_PART_SELECTION_AUTHORIZED.
# Coverage: tests/test_defined_risk_mode.py, which enumerates every regime and
# asserts no real-money selection can land on an UNDEFINED_RISK_FAMILY.
_DEFINED_RISK_MODE_AUTHORIZED = (
    "bujji/production_runtime/trading_session_governor/session_governor.py",
)

# AUTHORIZED CHANGE (2026-08-20, audit finding): close the learning loop.
# D-8 made every closed position durable, and governor_context_builder asks
# the AdaptiveRiskMemory real questions on every entry decision -- but the
# runner passed AdaptiveRiskMemory(), fresh and empty, every session, and
# append_observation had ZERO callers anywhere. The risk chain was
# interrogating a memory that could not answer.
#
#   bujji/production_runtime/risk_memory_bridge.py  NEW, additive: translates
#     durable OutcomeMemoryRecords into RiskMemoryEntries and hydrates the
#     memory at startup. Invents nothing -- volatility_regime is UNKNOWN and
#     named rather than back-filled from today's regime; mfe/mae stay None
#     rather than derived from realized_pnl. An unreadable store degrades to
#     exactly the empty memory Bujji had before, so it can never end a session.
#
# No existing module's behaviour changes, and with no closed positions the
# store is empty and the hydration is a no-op. Coverage:
# tests/test_learning_loop.py.
_LEARNING_LOOP_AUTHORIZED = (
    "bujji/production_runtime/risk_memory_bridge.py",
)


# AUTHORIZED 2026-08-21 (Chief Engineer mandate, Layer 1/2 audit P0-8): the
# market-data quality gate.
#
# WHY A NEW FILE IN A PROTECTED PACKAGE. Nothing stood between market data
# and a trading decision. Tracing the entire runner for a quality gate found
# exactly one string, in one branch, for one condition. Meanwhile
# MarketDataAdapter had always computed health_status and missing_fields on
# every snapshot, and IntelligenceCycleRecorder had always recorded the value
# under "market_snapshot_health" -- where nothing read it. The signal existed
# and was wired to a log line.
#
# WHAT IT DOES NOT DO. It adds no strategy, no construction rule, no risk
# threshold, and it modifies no existing module in these protected packages.
# It is additive and it only ever REFUSES -- it can turn a trade into a
# no-trade and never the reverse, so no existing decision path is loosened.
#
# See tests/test_market_data_quality_gate.py.
_DATA_QUALITY_GATE_AUTHORIZED = (
    "bujji/production_runtime/market_data_gate.py",
)


# AUTHORIZED 2026-08-21 (operator directive: "wire the websocket into the
# trading runner"). WebsocketTickProvider joins the existing provider family
# in intraday_price_provider.py -- the same seam Historical/LiveTickProvider
# already occupy, so the runner swaps an implementation instead of growing a
# data path. Read-only market data: the provider imports no order path and
# the feed it wraps cannot place, modify or cancel anything. Existing
# providers in the file are unmodified. See
# tests/test_websocket_tick_provider.py.
_WEBSOCKET_TICK_PROVIDER_AUTHORIZED = (
    "bujji/production_runtime/intraday_price_provider.py",
)


# AUTHORIZED 2026-08-21 (Chief Engineer mandate, Layer 11 audit): journaled
# order execution.
#
# WHY. A complete order state machine already existed in Gate A --
# MINTED/CONSTRUCTED/SUBMIT_INTENT/SUBMIT_ACK/FILL_OBSERVED, idempotency-keyed,
# transactional, with per-leg crash recovery against broker truth. It never
# ran: process_entry_cycle placed orders in a bare loop, both journal
# databases held ZERO rows, recover_group had ZERO callers, and the
# composition root carried `journal` unused. A partially-filled multi-leg
# entry therefore left a naked short that was also invisible to the runner.
#
# WHAT THIS ADDS. Only wiring. Every event shape is copied from the journal's
# own test suite and from msi_entry_bridge's already-written construction
# path -- no new state machine, no new strategy, no new risk rule. It only
# ever adds refusals and unwinds: an entry that cannot be journaled is not
# placed, and a partial fill is contained. See tests/test_journaled_execution.py.
_JOURNALED_EXECUTION_AUTHORIZED = (
    "bujji/production_runtime/execution_journal_bridge.py",
)


def test_no_forbidden_module_imports():
    out = _grep(FORBIDDEN_IMPORTS, RECORDER_FILE)
    assert out == "", f"forbidden import found: {out}"


def test_no_broker_order_position_margin_calls():
    out = _grep(
        r"\.(place_order|modify_order|cancel_order|get_open_positions|get_positions|"
        r"get_margin|get_funds|connect)\(",
        RECORDER_FILE,
    )
    assert out == "", f"forbidden call found: {out}"


def test_strategy_selection_assessment_has_no_execution_shaped_fields():
    from bujji.msi_strategy_selector.models import StrategySelectionAssessment
    field_names = set(StrategySelectionAssessment.__dataclass_fields__.keys())
    forbidden = {
        "entry", "exit", "strike", "quantity", "order", "capital",
        "risk_approval", "position", "option_type",
    }
    assert not (field_names & forbidden), f"forbidden fields found: {field_names & forbidden}"


def test_trade_intent_assessment_has_no_execution_shaped_fields():
    from bujji.msi_trade_intent.models import TradeIntentAssessment
    field_names = set(TradeIntentAssessment.__dataclass_fields__.keys())
    forbidden = {
        "entry", "exit", "strike", "quantity", "order", "capital",
        "risk_approval", "position", "option_type",
    }
    assert not (field_names & forbidden), f"forbidden fields found: {field_names & forbidden}"


def test_strategy_suitability_assessment_has_no_execution_shaped_fields():
    from bujji.msi_strategy_selection_foundation.models import StrategySuitabilityAssessment
    field_names = set(StrategySuitabilityAssessment.__dataclass_fields__.keys())
    forbidden = {
        "entry", "exit", "strike", "quantity", "order", "capital",
        "risk_approval", "position", "option_type",
    }
    assert not (field_names & forbidden), f"forbidden fields found: {field_names & forbidden}"


def test_newly_called_engines_unmodified():
    """msi_strategy_selector and msi_trade_intent remain exactly as they
    were when first wired -- this recorder only ever CALLS them.

    msi_strategy_selection_foundation is deliberately EXCLUDED from this
    "fully unmodified" check as of Phase 9's Liquidity Intelligence
    Bridge -- unlike every other bridge built this session, adding a
    real per-call liquidity parameter genuinely required modifying that
    package itself (engine.py's function signatures/logic, taxonomy.py's
    domain lists), not just calling existing unmodified functions. This
    was flagged in advance as a qualitatively different, more invasive
    change and required (and received) separate explicit approval
    before being made -- see test_strategy_selection_foundation_change_
    is_scoped_to_liquidity_bridge below for the narrower guarantee that
    now applies to it instead.

    Phase 14B ADDITION: msi_trade_intent/engine.py was deliberately,
    additively modified (a new `determine_trade_intent_from_selection`
    function; the legacy `determine_trade_intent` is byte-identical --
    see tests/test_phase14b_safety.py and
    docs/PHASE_14B_DECISION_PIPELINE_ARCHITECTURE.md). msi_strategy_selector
    remains fully unmodified and is still checked strictly below."""
    result = subprocess.run(
        ["git", "diff", "--stat", "360c003", "--", "bujji/msi_strategy_selector/"],
        cwd="/opt/bujji/app", capture_output=True, text=True,
    )
    assert result.stdout.strip() == "", f"a newly-consumed engine was modified: {result.stdout}"


def test_strategy_selection_foundation_change_is_scoped_to_liquidity_bridge():
    """msi_strategy_selection_foundation WAS deliberately modified
    (Phase 9), but only its two declarative/pure-function files, and
    only those two -- no test files, no other package touched under its
    cover, no execution/order/risk logic introduced."""
    result = subprocess.run(
        ["git", "diff", "--name-only", "360c003", "--", "bujji/msi_strategy_selection_foundation/"],
        cwd="/opt/bujji/app", capture_output=True, text=True,
    )
    changed = sorted(l for l in result.stdout.strip().splitlines() if l)
    # Baseline advanced to 360c003 (2026-08-19 audited backlog commit): the
    # Phase 9 engine.py/taxonomy.py changes this guard used to enumerate are
    # now INSIDE the baseline, so the protected expectation becomes "nothing
    # further" -- the guard keeps forbidding any new unauthorized change to
    # this package, which was always its point.
    assert changed == [], f"unexpected file set changed in msi_strategy_selection_foundation: {changed}"


def test_no_protected_execution_lineage_touched():
    result = subprocess.run(
        ["git", "diff", "--name-only", "360c003"],
        cwd="/opt/bujji/app", capture_output=True, text=True,
    )
    changed = [l for l in result.stdout.strip().splitlines() if l]
    changed = [l for l in changed if l not in _PAPERBROKER_V2_AUTHORIZED + _LOT_SIZE_AUTHORITATIVE_AUTHORIZED + _CPC_SAFETY_SPINE_AUTHORIZED + _CPC_EVIDENCE_AND_REALISM_AUTHORIZED + _LIVE_PREMIUM_FIX_AUTHORIZED + _THREE_PART_SELECTION_AUTHORIZED + _DEFINED_RISK_MODE_AUTHORIZED + _LEARNING_LOOP_AUTHORIZED + _DATA_QUALITY_GATE_AUTHORIZED + _WEBSOCKET_TICK_PROVIDER_AUTHORIZED + _JOURNALED_EXECUTION_AUTHORIZED]
    forbidden_prefixes = (
        "bujji/msi_trade_construction/", "bujji/risk_governor/", "bujji/execution_engine/",
        "bujji/trading_brain/", "bujji/msi_shadow_trading/", "bujji/mic_replay/",
        "bujji/production_runtime/",
    )
    violations = [l for l in changed if any(l.startswith(p) for p in forbidden_prefixes)]
    assert violations == [], f"forbidden module changes found: {violations}"
