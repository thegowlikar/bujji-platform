"""Safety verification -- Shadow Trading Brain, Phase 5B.

Confirms domain_view_adapter.py stays a pure, boring translator: no
strategy/trade/execution/risk/broker/MIC-v2/trading_brain import or
call, and that no decision module beyond msi_consensus/
msi_decision_synthesis was touched or invoked in production code this
phase.
"""
from __future__ import annotations

import subprocess

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


FILE = "bujji/market_state/domain_view_adapter.py"

FORBIDDEN_IMPORTS = (
    r"^\s*(from|import)\s+(bujji\.)?(trading_brain|execution_engine|risk_governor|"
    r"msi_strategy_selector|msi_trade_intent|msi_trade_thesis|msi_strategy_eligibility|"
    r"msi_shadow_trading|execution_integration|broker|mic_replay|mic_v2)\b"
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
    # OI-expansion lenses: positioning had been decided by 3 of 5 lenses
    # because nothing ever passed a previous snapshot.
    "bujji/market_state_builder/assessment_bridge.py",
    "bujji/market_state_builder/market_state.py",
    "bujji/msi_market_direction/config.py",
)


# AUTHORIZED 2026-08-21 (Chief Engineer mandate, Layer 1/2 audit P0-8): the
# market-data quality gate. Additive, refuses only -- it can turn a trade
# into a no-trade and never the reverse, and it modifies no existing module
# in a protected package. See tests/test_market_data_quality_gate.py.
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


def test_no_forbidden_module_imports_in_adapter():
    out = _grep(FORBIDDEN_IMPORTS, FILE)
    assert out == "", f"forbidden import found: {out}"


def test_no_broker_or_order_calls_in_adapter():
    out = _grep(
        r"\.(place_order|modify_order|cancel_order|get_open_positions|get_positions|"
        r"get_margin|get_funds|connect|get_quote|get_spot|get_vix)\(",
        FILE,
    )
    assert out == "", f"forbidden call found: {out}"


def test_adapter_only_produces_the_two_generic_types_no_new_dataclass():
    out = _grep(r"^\s*@dataclass", FILE)
    assert out == "", f"unexpected new dataclass defined in adapter: {out}"


def test_no_strategy_selection_or_thesis_modules_imported():
    out = _grep(
        r"^\s*(from|import)\s+(bujji\.)?(msi_trade_thesis|msi_trade_intent|"
        r"msi_strategy_eligibility|msi_strategy_selector)\b",
        FILE,
    )
    assert out == "", f"strategy/thesis/intent module imported -- out of Phase 5B scope: {out}"


# Phase 14B deliberately, explicitly modified exactly these two files
# (a purely additive config.py extension + a purely additive new
# function in engine.py) -- see docs/PHASE_14B_DECISION_PIPELINE_ARCHITECTURE.md
# and tests/test_phase14b_safety.py for the full rationale and the
# narrower guarantees that now apply to them instead.
_PHASE14B_EXCEPTION = (
    "bujji/msi_decision_synthesis/config.py",
    "bujji/msi_trade_intent/engine.py",
)


def test_msi_consensus_and_decision_synthesis_engines_unmodified():
    result = subprocess.run(
        ["git", "diff", "--stat", "360c003", "--",
         "bujji/msi_consensus/", "bujji/msi_decision_synthesis/",
         "bujji/msi_market_direction/", "bujji/msi_market_structure/",
         "bujji/msi_price_structure/", "bujji/msi_participant_positioning/",
         "bujji/msi_volatility_structure/"],
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
    assert unexpected == [], f"a reused MSI engine package was modified: {unexpected}"


def test_no_decision_modules_beyond_consensus_and_synthesis_touched():
    result = subprocess.run(
        ["git", "diff", "--name-only", "360c003"],
        cwd="/opt/bujji/app", capture_output=True, text=True,
    )
    changed = [l for l in result.stdout.strip().splitlines() if l]
    changed = [l for l in changed if l not in _PAPERBROKER_V2_AUTHORIZED + _LOT_SIZE_AUTHORITATIVE_AUTHORIZED + _CPC_SAFETY_SPINE_AUTHORIZED + _CPC_EVIDENCE_AND_REALISM_AUTHORIZED + _THREE_PART_SELECTION_AUTHORIZED + _DEFINED_RISK_MODE_AUTHORIZED + _LEARNING_LOOP_AUTHORIZED + _DIRECTION_POSITIONING_LENS_AUTHORIZED + _DATA_QUALITY_GATE_AUTHORIZED + _WEBSOCKET_TICK_PROVIDER_AUTHORIZED + _JOURNALED_EXECUTION_AUTHORIZED + _EXIT_BROKER_TRUTH_AUTHORIZED + _EOD_CLOSURE_AUTHORIZED + _POSITION_RECONCILIATION_AUTHORIZED]
    forbidden_prefixes = (
        "bujji/msi_trade_thesis/", "bujji/msi_trade_intent/",
        "bujji/msi_strategy_eligibility/", "bujji/msi_strategy_selector/",
        "bujji/msi_shadow_trading/", "bujji/trading_brain/", "bujji/execution_engine/",
        "bujji/risk_governor/", "bujji/mic_replay/", "bujji/production_runtime/",
    )
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
    ]
    assert violations == [], f"forbidden module changes found: {violations}"


def test_shadow_session_runner_and_broker_unchanged():
    # A prose mention in shadow_session_runner.py's own docstring is
    # expected and correct as of the Continuous Intelligence Observatory
    # phase (it documents intelligence_cycle_recorder.py, which itself
    # imports domain_view_adapter) -- this test only guards against an
    # actual import/call of domain_view_adapter appearing directly in
    # shadow_session_runner.py itself, which would bypass the recorder.
    out = _grep(
        r"^\s*(from|import).*domain_view_adapter|domain_view_adapter\.",
        "bujji/shadow_runtime/shadow_session_runner.py",
    )
    assert out == "", f"unexpected direct domain_view_adapter coupling: {out}"
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
    #
    # Updated 2026-08-21 (Rule 13, EOD closure): TWO additive declarations, no
    # behaviour change and no new capability.
    #
    #   order_book_survives_restart = True -- startup recovery must know
    #   whether a broker's not-found is evidence about the exchange or merely
    #   this process's amnesia. PaperBroker rebuilds its order dict empty every
    #   session, so a not-found from IT was being recorded durably as
    #   RECOVERY_CONFIRMED_NEVER_RECEIVED. The exchange holds this broker's
    #   book, so its not-found IS evidence.
    #
    #   FYERS_POSITION_SCHEMA_VERIFIED = False -- get_open_positions() reads
    #   `netQty`/`netAvg` off each netPositions row. The top-level shape was
    #   confirmed live against an EMPTY book; the per-row names have never been
    #   seen against a real open position, as that method already states. EOD
    #   flat verification, residual sizing and orphan detection all rest on
    #   them, and if `netQty` were named otherwise every row would read qty 0
    #   and the account would look EMPTY -- a silent failure that manufactures
    #   flatness. This makes that a gate rather than a comment, flippable only
    #   by an operator observing a REAL open position.
    #
    # No place/modify/cancel surface is touched; the two capability guards
    # remain untouched and enforcing. Coverage: tests/test_eod_closure.py.
    assert ("303" in stat_line or "55" in stat_line or "81" in stat_line
            or stat_line == ""), \
        f"unexpected fyers.py diff: {stat_line}"
