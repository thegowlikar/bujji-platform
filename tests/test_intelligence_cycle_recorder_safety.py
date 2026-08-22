"""Safety verification -- Continuous Intelligence Observatory.

Confirms the recorder and its wiring into ShadowSessionRunner never
import/call Strategy Selector, Trade Intent, Trade Construction, Risk
Governor, Execution, or any broker order/position/margin method -- and
that every bridge it calls remains unmodified.
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
RUNNER_FILE = "bujji/shadow_runtime/shadow_session_runner.py"

FORBIDDEN_IMPORTS = (
    r"^\s*(from|import)\s+(bujji\.)?(trading_brain|execution_engine|risk_governor|"
    # msi_strategy_selector and msi_trade_intent REMOVED from this list
    # 2026-08-06 -- now legitimately imported (description-only outputs,
    # no execution capability -- see
    # test_intelligence_cycle_recorder_extended_safety.py for their own
    # dedicated, still-strict safety coverage).
    r"msi_trade_construction|"
    r"msi_strategy_eligibility_foundation|msi_shadow_trading|execution_integration|"
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

# AUTHORIZED CHANGE (2026-08-20, operator directive): open interest reaches
# the direction read. Direction was derived from exactly two lenses, price
# structure and market structure, and BOTH read the same evidence -- NIFTY
# spot price polled every 30 seconds. One instrument, one field. When they
# disagreed the answer was UNKNOWN, which is what the first live continuous
# session reported for most of 2026-08-20.
#
# MPPI was already computing five lenses over ~199,000 option rows a day and
# reaching the THESIS, invisible to direction. OPTIONS_POSITIONING_DIRECTION
# had been sitting in KNOWN_LENS_NAMES unfilled the whole time.
#
#   bujji/msi_market_direction/engine.py   + derive_participant_positioning_lens
#     and an OPTIONAL mppi parameter (default None -> UNKNOWN opinion), so every
#     existing caller and test keeps working. No existing lens is touched and
#     the reconciliation rule is unchanged: conflicting lenses still yield
#     MIXED/UNKNOWN rather than an average.
#   bujji/market_state/direction_bridge.py  passes the assessment through.
#
# NO INVERSION: MPPI's bias is already normalised to PRICE direction (verified
# in derive_writer_dominance_lens -- call writers dominant yields
# BEARISH_POSITIONING). Confidence is capped at MODERATE because positioning
# is intent, not a fact about price; HIGH stays reserved for MSSI's structural
# breakout/breakdown. MIXED_POSITIONING becomes UNKNOWN, never NEUTRAL.
#
# Coverage: tests/test_direction_positioning_lens.py.
_DIRECTION_POSITIONING_LENS_AUTHORIZED = (
    "bujji/msi_market_direction/engine.py",
    "bujji/market_state/direction_bridge.py",
    # Extended 2026-08-20 (same directive): the futures BASIS-CHANGE lens.
    # Basis level is a calendar artifact -- NIFTY futures carry a premium
    # that decays to expiry -- so the lens reads the CHANGE, which needs a
    # previous observation. Plumbing that through also supplied MPPI's
    # previous chain for the first time, un-darkening its OI-migration and
    # OI-expansion lenses.
    "bujji/market_state_builder/assessment_bridge.py",
    "bujji/market_state_builder/market_state.py",
    "bujji/msi_market_direction/config.py",
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


# AUTHORIZED 2026-08-21 (Chief Engineer mandate, bypass audit): exits use the
# same broker-truth machine as entries.
#
# WHY. The bypass audit found the exit path still calling broker.place_order
# directly -- no journal, no poll-to-terminal, no cancel-on-timeout -- while
# the entry path had been upgraded. That is strictly MORE dangerous than the
# entry case, because an exit failure happens while a naked position is
# already live. Three defects on that live path: PENDING/UNKNOWN was mapped
# to STATUS_REJECTED (telling the runner an exit FAILED while it was still
# working), LIFECYCLE_ORDER_FILLED was published unconditionally without
# reading result.is_filled, and closure could be marked from a broker read
# taken while an exit order was unsettled.
#
# WHAT THIS ADDS. Only routing and honesty: an optional place_fn (default
# None, so every existing construction site keeps its exact prior behaviour),
# a STATUS_UNKNOWN that no longer masquerades as rejection, and telemetry
# that matches the order result. No new strategy, no new risk rule, and the
# change only ever makes the reported state LESS certain, never more.
# See tests/test_exit_broker_truth.py.
_EXIT_BROKER_TRUTH_AUTHORIZED = (
    "bujji/production_runtime/trade_lifecycle_executor.py",
)


# AUTHORIZED 2026-08-21 (Rule 11): EOD closure as a broker-truth state machine.
#
# WHY. _eod_close() ran one management pass and then called
# run_market_close_sequence() -- four lines that transition POSTMARKET then
# COMPLETE. Nothing discovered broker positions, nothing cancelled working
# orders, and nothing asked the broker whether the account was flat before the
# session declared COMPLETE and the process exited. A position the management
# pass did not close carried overnight with nothing watching it, while
# finalize_session(final_positions=(), unrealized_pnl=0.0) wrote a summary
# asserting there was nothing open.
#
# WHAT IT ADDS. Orchestration only, composed from proven parts: placement goes
# through the SAME broker-truth place_fn entries and exits use (never
# place_order directly), the reversal pattern is lifted from
# core/orchestrator._flatten_orphan, and discovery is an unfiltered
# get_open_positions. It adds no strategy, no risk rule, and no new broker
# capability -- and it can only ever REFUSE to complete a session, never
# permit one it previously refused. See tests/test_eod_closure.py.
_EOD_CLOSURE_AUTHORIZED = (
    "bujji/production_runtime/eod_closure.py",
)


# AUTHORIZED 2026-08-21: continuous broker-truth position reconciliation.
#
# WHY. Broker truth was consulted at placement, at startup and at EOD -- never
# in between. The only in-session position read went through
# PositionRealityRegistry, which INTERSECTS the broker's positions with an
# in-memory table of registered symbols, so a position at a symbol Bujji never
# registered was mathematically undiscoverable through that API: never valued,
# never stop-lossed, never escalated, unnoticed until EOD.
#
# WHAT IT ADDS. A pure comparison function (no I/O, no clock) plus a runner
# hook on the EXISTING management cadence. It reports and gates; it never
# mutates position state -- a test asserts mark_closed / place_order /
# register_entry / _execute_reduce never appear in it. Closure stays owned by
# the executor, governor and registry. It can only ever REFUSE new risk, never
# permit risk that was previously refused. The unfiltered read reuses
# eod_closure.discover_broker_positions rather than adding a second one.
# Coverage: tests/test_position_reconciliation.py.
_POSITION_RECONCILIATION_AUTHORIZED = (
    "bujji/production_runtime/position_reconciliation.py",
)


# AUTHORIZED 2026-08-21: PositionRealityRegistry.symbols_for_group() -- one
# PURE accessor, no new capability.
#
# WHY. Reconciliation (d6fbfaf) derived EXPECTED via get_group_reality() per
# group, and that re-reads get_open_positions() on EVERY call to compute
# is_open. So reconciling N groups issued N+1 broker reads AND -- the real
# hazard -- derived EXPECTED from reads 1..N while OBSERVED came from read
# N+1. A position closing between them dropped its symbol out of EXPECTED
# while it still appeared in OBSERVED: a manufactured BROKER_ONLY, which is
# the CRITICAL finding that blocks new risk. A safety check that can invent
# its own alarm is worse than no check.
#
# WHAT IT ADDS. A read-only accessor returning the group's already-stored
# symbols tuple. It calls no broker method (asserted over the AST, not the
# source text), adds no mutation surface, and lets reconciliation derive both
# sides of its comparison from a SINGLE read.
# Coverage: tests/test_position_reconciliation.py::TestOneReadNotNPlusOne.
_REGISTRY_PURE_ACCESSOR_AUTHORIZED = (
    "bujji/production_runtime/position_reality_registry.py",
)

# OPERATOR AUTHORIZATION 2026-08-21 -- the single option-symbol vocabulary.
#
# WHY THESE THREE, AND WHY THEY ARE UNDER A PROTECTED PREFIX. Two vocabularies
# described the same contract: the chain carried the broker's own symbol
# ("NSE:NIFTY2681824100CE") while every downstream consumer REBUILT its own
# from the strategy leg ("NIFTY2026-08-2524500CE"). Measured live 2026-08-20:
#
#     'NIFTY2026-08-2524500CE'  -> verified=False  total_margin=None
#     'NSE:NIFTY26AUG22700PE'   -> verified=True   total_margin=98915.87
#
# The API was never broken; every entry that session was blocked by a string
# format, and the same split made every paper fill frictionless because the
# quote book was keyed one way and orders looked up the other.
#
#   option_symbol_resolver.py  NEW. The only place a broker symbol is now
#                              OBTAINED. Lookup-only: it cannot construct,
#                              prefix, strip, reformat, round or coerce, and
#                              a test asserts that structurally over its AST.
#   live_chain_provider.py     Tags FYERS rows BROKER_AUTHORITATIVE and stops
#                              fabricating a symbol when FYERS omits one --
#                              that `or` wrote a THIRD invented format into
#                              the exact field Gate B trusts as broker-real.
#   store_chain_provider.py    Stops fabricating instrument_symbol entirely.
#                              The capture never recorded broker symbols, so
#                              it now emits an explicit non-tradable sentinel
#                              with provenance ABSENT and fails closed.
#
# WHAT IT DOES NOT DO. It adds no execution surface, no new broker call, and
# no new mutation path. Every one of the three either REMOVES a construction
# site or refuses where it previously invented. The net change to production
# behaviour is that a symbol nobody could verify is now refused instead of
# sent.
# Coverage: tests/test_option_symbol_resolver.py (39),
#           tests/test_single_symbol_vocabulary.py (23),
#           tests/test_symbol_provenance.py (36), each with negative controls.
# =========================================================================
# LIVE-READINESS CAMPAIGN (2026-08-22) -- ONE new protected-package file.
#
# WHAT IT IS. `session_safety_verdict.py` is a PURE function over the session
# summary dict the runner already builds. It reads `final_positions_status`,
# `closure_reason`, `closure_truths_agree`, `emergency_close_*`,
# `has_unresolved_exit` and `position_truth_established`, and returns
# safe/unsafe plus reasons. No I/O, no clock, no broker, no mutation -- it does
# not even mutate its input, and a test pins that.
#
# WHY IT EXISTS. `_run_session` returned EXIT_OK the moment `runner.run()`
# returned, without reading a single field of the summary. A session could end
# with closure_reason=CRITICAL_UNFLATTENED_POSITION and still exit 0, so
# systemd saw success, `OnFailure=` never fired, and the alarm that exists
# reached nobody. That is the 2026-08-21 session verbatim: an alert landed only
# because an unrelated websocket hang got the process SIGTERM-killed 21 minutes
# after it had logged SHUTDOWN.
#
# WHAT IT DOES NOT DO. It adds NO execution surface, no broker call, no order
# path, no mutation and no new state. It cannot cause a trade; it can only
# cause a non-zero exit code. Every fact it grades was already computed and
# already logged at CRITICAL by the runner -- this file is the wire from those
# detectors to systemd, nothing more.
#
# The other production_runtime file this campaign touched,
# live_chain_provider.py, is already named in the prior phase's tuples and is
# deliberately NOT re-authorised here.
#
# Coverage: tests/test_session_safety_exit_code.py (15) and
#           tests/test_position_truth_before_entry.py (9), both with negative
#           controls that were run and observed to fire.
# =========================================================================
_LIVE_READINESS_AUTHORIZED = (
    "bujji/production_runtime/session_safety_verdict.py",
)

_SYMBOL_VOCABULARY_AUTHORIZED = (
    "bujji/production_runtime/option_symbol_resolver.py",
    "bujji/production_runtime/live_chain_provider.py",
    "bujji/production_runtime/store_chain_provider.py",
)


def test_no_forbidden_module_imports_in_recorder():
    out = _grep(FORBIDDEN_IMPORTS, RECORDER_FILE)
    assert out == "", f"forbidden import found: {out}"


def test_no_broker_order_position_margin_calls_in_recorder():
    out = _grep(
        r"\.(place_order|modify_order|cancel_order|get_open_positions|get_positions|"
        r"get_margin|get_funds|connect)\(",
        RECORDER_FILE,
    )
    assert out == "", f"forbidden call found: {out}"


def test_no_strategy_selector_or_trade_intent_referenced_in_runner():
    out = _grep(
        r"(msi_strategy_selector|msi_trade_intent|msi_trade_construction|risk_governor|execution_engine)",
        RUNNER_FILE,
    )
    assert out == "", f"forbidden reference found in runner: {out}"


def test_no_new_dataclass_defined_recorder_stays_a_thin_orchestrator():
    out = _grep(r"^\s*@dataclass", RECORDER_FILE)
    assert out == "", f"unexpected new dataclass defined: {out}"


# Phase 14B deliberately, additively modified exactly these two files --
# see docs/PHASE_14B_DECISION_PIPELINE_ARCHITECTURE.md and
# tests/test_phase14b_safety.py.
_PHASE14B_EXCEPTION = (
    "bujji/msi_decision_synthesis/config.py",
    "bujji/msi_trade_intent/engine.py",
)


def test_all_consumed_bridges_and_engines_unmodified_this_phase():
    result = subprocess.run(
        ["git", "diff", "--stat", "360c003", "--",
         "bujji/market_state/direction_bridge.py", "bujji/market_state/synthesizer.py",
         "bujji/market_state/domain_view_adapter.py", "bujji/market_state/trade_thesis_bridge.py",
         "bujji/market_state/strategy_eligibility_bridge.py",
         "bujji/market_perception/msi_adapter.py", "bujji/market_perception/intelligence_adapter.py",
         "bujji/market_state_builder/market_state.py",
         "bujji/msi_consensus/", "bujji/msi_decision_synthesis/",
         "bujji/msi_trade_thesis/", "bujji/msi_strategy_eligibility/"],
        cwd="/opt/bujji/app", capture_output=True, text=True,
    )
    changed_files = [l.split("|")[0].strip() for l in result.stdout.strip().splitlines() if l and "|" in l]
    # AUTHORIZED 2026-08-20 (operator directive): the positioning lens.
    # msi_market_direction/engine.py and market_state/direction_bridge.py
    # gained OPTIONS_POSITIONING_DIRECTION -- the lens slot that had been in
    # KNOWN_LENS_NAMES unfilled since the package was written. Direction had
    # been derived from two lenses that BOTH read spot price alone. The mppi
    # parameter is OPTIONAL (default None), no existing lens is touched, and
    # the reconciliation rule is unchanged. See
    # _DIRECTION_POSITIONING_LENS_AUTHORIZED above and
    # tests/test_direction_positioning_lens.py.
    unexpected = [f for f in changed_files
                  if f not in _PHASE14B_EXCEPTION
                  and f not in _DIRECTION_POSITIONING_LENS_AUTHORIZED]
    assert unexpected == [], f"a reused bridge/engine was modified: {unexpected}"


def test_no_protected_lineage_package_touched():
    result = subprocess.run(
        ["git", "diff", "--name-only", "360c003"],
        cwd="/opt/bujji/app", capture_output=True, text=True,
    )
    changed = [l for l in result.stdout.strip().splitlines() if l]
    changed = [l for l in changed if l not in _PAPERBROKER_V2_AUTHORIZED + _LOT_SIZE_AUTHORITATIVE_AUTHORIZED + _CPC_SAFETY_SPINE_AUTHORIZED + _CPC_EVIDENCE_AND_REALISM_AUTHORIZED + _LIVE_PREMIUM_FIX_AUTHORIZED + _THREE_PART_SELECTION_AUTHORIZED + _DEFINED_RISK_MODE_AUTHORIZED + _LEARNING_LOOP_AUTHORIZED + _DIRECTION_POSITIONING_LENS_AUTHORIZED + _DATA_QUALITY_GATE_AUTHORIZED + _WEBSOCKET_TICK_PROVIDER_AUTHORIZED + _JOURNALED_EXECUTION_AUTHORIZED + _EXIT_BROKER_TRUTH_AUTHORIZED + _EOD_CLOSURE_AUTHORIZED + _POSITION_RECONCILIATION_AUTHORIZED + _REGISTRY_PURE_ACCESSOR_AUTHORIZED + _SYMBOL_VOCABULARY_AUTHORIZED + _LIVE_READINESS_AUTHORIZED]
    forbidden_prefixes = (
        "bujji/msi_strategy_selector/", "bujji/msi_trade_intent/", "bujji/msi_trade_construction/",
        "bujji/risk_governor/", "bujji/execution_engine/", "bujji/trading_brain/",
        "bujji/msi_shadow_trading/", "bujji/mic_replay/", "bujji/production_runtime/",
    )
    # bujji/broker/ deliberately excluded here -- its only diff
    # (get_futures_quote(), fyers.py) predates this phase and was
    # already approved/verified in Phase 1; this test only guards
    # against THIS phase touching a forbidden lineage.
    violations = [
        l for l in changed
        if any(l.startswith(p) for p in forbidden_prefixes)
        and l not in _PHASE14B_EXCEPTION
        and l not in _DATA_QUALITY_GATE_AUTHORIZED
        and l not in _WEBSOCKET_TICK_PROVIDER_AUTHORIZED
        and l not in _JOURNALED_EXECUTION_AUTHORIZED
        and l not in _EXIT_BROKER_TRUTH_AUTHORIZED
        and l not in _EOD_CLOSURE_AUTHORIZED
        and l not in _POSITION_RECONCILIATION_AUTHORIZED
        and l not in _REGISTRY_PURE_ACCESSOR_AUTHORIZED
        and l not in _LIVE_READINESS_AUTHORIZED
    ]
    assert violations == [], f"forbidden module changes found: {violations}"


def test_shadow_session_runner_diff_has_no_removed_code():
    """UPDATED, Phase 19.2.2: Phase 19.2.2 makes LiquidityBrain.analyze()
    require a new `context: IntelligenceContext` argument (clock injection
    -- see docs/PHASE_19_2_2_INTELLIGENCE_DETERMINISM_HARDENING_IMPLEMENTATION.md).
    Every caller, including this one, must pass it. The one line this
    diff removes is the OLD analyze() call being reformatted onto
    multiple lines to carry the new `context=` kwarg -- the same call, on
    the same object, with the same four positional arguments, not
    different logic. That single known-safe rewrite is allow-listed by
    exact substring below; anything else removed still fails this test."""
    KNOWN_SAFE_REMOVED_LINE = (
        "reading = self._liquidity_brain.analyze(pair.ce_leg.bid, pair.ce_leg.ask, pair.pe_leg.bid, pair.pe_leg.ask)"
    )
    result = subprocess.run(
        ["git", "diff", "360c003", "--", RUNNER_FILE],
        cwd="/opt/bujji/app", capture_output=True, text=True,
    )
    removed = [
        l for l in result.stdout.splitlines()
        if l.startswith("-") and not l.startswith("---") and l.strip() != "-"
    ]
    code_removed = [
        l for l in removed
        if any(token in l for token in ("def ", "class ", "self.", "await ", "return ", "import "))
        and KNOWN_SAFE_REMOVED_LINE not in l
    ]
    assert code_removed == [], f"unexpected removed CODE lines: {code_removed}"


def test_intelligence_cycle_enabled_defaults_to_false():
    import sys
    sys.path.insert(0, "/opt/bujji/app")
    import inspect
    from bujji.shadow_runtime.shadow_session_runner import ShadowSessionRunner
    sig = inspect.signature(ShadowSessionRunner.__init__)
    assert sig.parameters["intelligence_cycle_enabled"].default is False
