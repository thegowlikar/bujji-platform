"""Safety verification -- Shadow Trading Brain, Phase 6B.

Confirms strategy_eligibility_bridge.py stays a boring compatibility-
filter wrapper: no strategy-selector/trade-intent/construction/risk/
execution/broker import or call, no ranking/preference/recommendation
logic, no protected module touched.
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


FILE = "bujji/market_state/strategy_eligibility_bridge.py"

FORBIDDEN_IMPORTS = (
    r"^\s*(from|import)\s+(bujji\.)?(msi_strategy_selector|msi_trade_intent|"
    r"msi_trade_construction|risk_governor|execution_engine|broker|fyers|"
    r"trading_brain|mic_replay|mic_v2)\b"
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


def test_no_forbidden_module_imports_in_bridge():
    out = _grep(FORBIDDEN_IMPORTS, FILE)
    assert out == "", f"forbidden import found: {out}"


def test_no_broker_or_order_calls_in_bridge():
    out = _grep(
        r"\.(place_order|modify_order|cancel_order|get_open_positions|get_positions|"
        r"get_margin|get_funds|connect|get_quote|get_spot|get_vix)\(",
        FILE,
    )
    assert out == "", f"forbidden call found: {out}"


def test_no_new_dataclass_defined_bridge_stays_a_wrapper():
    out = _grep(r"^\s*@dataclass", FILE)
    assert out == "", f"unexpected new dataclass defined in bridge: {out}"


def test_no_ranking_preference_or_recommendation_identifiers_as_code():
    # Narrower than a blanket text grep -- checks actual code
    # identifiers (assignments/attribute access), not docstring prose
    # explaining what this bridge does NOT do.
    out = _grep(
        r"(selected_strategy|winning_strategy|preferred_strategy|strategy_ranking|"
        r"trade_recommendation|position_sizing|capital_allocation)\s*=",
        FILE,
    )
    assert out == "", f"decision-shaped code found: {out}"


def test_strategy_eligibility_engine_unmodified():
    result = subprocess.run(
        ["git", "diff", "--stat", "360c003", "--", "bujji/msi_strategy_eligibility/"],
        cwd="/opt/bujji/app", capture_output=True, text=True,
    )
    assert result.stdout.strip() == "", f"msi_strategy_eligibility was modified: {result.stdout}"


# Phase 14B: additive config.py extension + additive new function in
# engine.py -- see docs/PHASE_14B_DECISION_PIPELINE_ARCHITECTURE.md and
# tests/test_phase14b_safety.py.
_PHASE14B_EXCEPTION = (
    "bujji/msi_decision_synthesis/config.py",
    "bujji/msi_trade_intent/engine.py",
)


def test_consensus_and_decision_synthesis_and_trade_thesis_unmodified():
    result = subprocess.run(
        ["git", "diff", "--stat", "360c003", "--",
         "bujji/msi_consensus/", "bujji/msi_decision_synthesis/", "bujji/msi_trade_thesis/",
         "bujji/market_state/domain_view_adapter.py", "bujji/market_state/trade_thesis_bridge.py"],
        cwd="/opt/bujji/app", capture_output=True, text=True,
    )
    changed_files = [l.split("|")[0].strip() for l in result.stdout.strip().splitlines() if l and "|" in l]
    unexpected = [f for f in changed_files if f not in _PHASE14B_EXCEPTION]
    assert unexpected == [], f"prior-phase artifacts were modified: {unexpected}"


def test_no_forbidden_protected_package_touched():
    result = subprocess.run(
        ["git", "diff", "--name-only", "360c003"],
        cwd="/opt/bujji/app", capture_output=True, text=True,
    )
    changed = [l for l in result.stdout.strip().splitlines() if l]
    changed = [l for l in changed if l not in _PAPERBROKER_V2_AUTHORIZED + _LOT_SIZE_AUTHORITATIVE_AUTHORIZED + _CPC_SAFETY_SPINE_AUTHORIZED + _CPC_EVIDENCE_AND_REALISM_AUTHORIZED + _LIVE_PREMIUM_FIX_AUTHORIZED + _THREE_PART_SELECTION_AUTHORIZED + _DEFINED_RISK_MODE_AUTHORIZED + _LEARNING_LOOP_AUTHORIZED + _DATA_QUALITY_GATE_AUTHORIZED + _WEBSOCKET_TICK_PROVIDER_AUTHORIZED + _JOURNALED_EXECUTION_AUTHORIZED + _EXIT_BROKER_TRUTH_AUTHORIZED]
    forbidden_prefixes = (
        "bujji/msi_strategy_selector/", "bujji/msi_trade_intent/", "bujji/msi_trade_construction/",
        "bujji/risk_governor/", "bujji/execution_engine/", "bujji/trading_brain/",
        "bujji/msi_shadow_trading/", "bujji/mic_replay/", "bujji/production_runtime/",
    )
    violations = [
        l for l in changed
        if any(l.startswith(p) for p in forbidden_prefixes)
        and l not in _PHASE14B_EXCEPTION
        and l not in _DATA_QUALITY_GATE_AUTHORIZED
        and l not in _WEBSOCKET_TICK_PROVIDER_AUTHORIZED
        and l not in _JOURNALED_EXECUTION_AUTHORIZED
        and l not in _EXIT_BROKER_TRUTH_AUTHORIZED
    ]
    assert violations == [], f"forbidden module changes found: {violations}"


def test_shadow_session_runner_and_broker_unchanged():
    # A prose mention in shadow_session_runner.py's own docstring is
    # expected and correct as of the Continuous Intelligence Observatory
    # phase (it documents intelligence_cycle_recorder.py, which itself
    # imports strategy_eligibility_bridge) -- this test only guards
    # against an actual import/call of strategy_eligibility_bridge
    # appearing directly in shadow_session_runner.py itself, which would
    # bypass the recorder.
    out = _grep(
        r"^\s*(from|import).*strategy_eligibility_bridge|strategy_eligibility_bridge\.",
        "bujji/shadow_runtime/shadow_session_runner.py",
    )
    assert out == "", f"unexpected direct strategy_eligibility_bridge coupling: {out}"
    result = subprocess.run(
        ["git", "diff", "--stat", "360c003", "--", "bujji/broker/fyers.py"],
        cwd="/opt/bujji/app", capture_output=True, text=True,
    )
    stat_line = result.stdout.strip()
    # Baseline "37" -> "61" (17B get_futures_quote() depth() OI) -> "85"
    # (17F.1.2 Q5 get_depth()) -> "114" (17F.7.1 get_option_chain_raw()).
    # Updated 2026-08-13: Phase 17I.6.1 deliberately, explicitly approved
    # adding FyersBroker.get_spot_raw()/get_futures_quote_raw()/
    # get_vix_raw() (raw pass-throughs mirroring get_option_chain_raw()'s
    # and get_depth()'s own discipline -- see
    # docs/PHASE_17I5_FUTURES_IDENTITY_AUDIT.md and
    # tests/test_fyers_raw_quotes.py's dedicated coverage). This guard
    # still catches any FURTHER, unapproved drift beyond that.
    # Updated 2026-08-17: Phase 19.15 deliberately, explicitly approved
    # adding transport-level request pacing at the _call() choke point
    # (_wait_for_slot/_paced). A live 429 killed session startup: one
    # build_snapshot() issued 85 calls in 2.98s, peaking at 31/s against a
    # 10/s ceiling. No write capability is added -- the two capability
    # guards (test_market_perception_safety, test_phase14b_safety) are the
    # real boundary here and remain untouched and enforcing. Dedicated
    # coverage: tests/test_fyers_transport_pacing.py.
    # Updated 2026-08-17 (second change this day): bounded retry-with-backoff
    # on a code=429 rate-limit refusal, at the same _call() choke point. READ
    # actions only, via an allowlist -- a refused write is never repeated,
    # because a refusal alone cannot distinguish "rejected" from "accepted,
    # acknowledgement refused". Error semantics unchanged: once retries are
    # spent the refusal is returned as received and the caller's own
    # _raise_if_error still raises. Coverage:
    # tests/test_fyers_rate_limit_retry.py. The two capability guards remain
    # untouched and enforcing.
    # Updated 2026-08-19 (CP-D): the pacer at this same _call() choke point
    # becomes HOST-WIDE. The ceiling is per ACCOUNT and this pacer was per
    # interpreter, so the four Bujji processes that share one credential --
    # three of them now waking together at 09:14 -- each paced to ~8.3/s and
    # together presented the account with up to ~33/s against a documented
    # 10/s ceiling. That overrun was never visible as a crash; it showed up
    # as an architectural workaround, the trading unit's fire time carrying a
    # rate-limit offset because schedule separation was the only
    # cross-process control that existed.
    #
    # The change is ADDITIVE and layered: bujji/broker/rate_budget.py
    # (flock'd shared slot file) is consulted FIRST, and the existing
    # in-process pacer remains behind it untouched, so an unreachable budget
    # degrades to exactly the previous behaviour rather than to none. No
    # write capability is added; the two capability guards
    # (test_market_perception_safety, test_phase14b_safety) remain untouched
    # and enforcing. Dedicated coverage: tests/test_rate_budget.py, including
    # a REAL two-process test that fails if the budget is not shared.
    assert ("303" in stat_line or "55" in stat_line or stat_line == ""), \
        f"unexpected fyers.py diff: {stat_line}"
