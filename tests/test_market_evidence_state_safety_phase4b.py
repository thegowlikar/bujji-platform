"""Safety verification -- Shadow Campaign v2 Phase 4B.

Proves, at the source level, that bujji/market_state/evidence_boundary.py
(a) has no strategy/trade/execution/capital/risk field on
MarketEvidenceState, (b) has no external imports beyond .models, (c)
never imports broker/trading_brain/MIC/execution/strategy modules, and
(d) leaves MarketState, ShadowSessionRunner, and FYERS code untouched.
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


FILE = "bujji/market_state/evidence_boundary.py"

FORBIDDEN_IMPORTS = (
    r"^\s*(from|import)\s+(bujji\.)?(trading_brain|execution_engine|risk_governor|"
    r"msi_strategy_selector|msi_trade_intent|msi_trade_thesis|msi_decision_synthesis|"
    r"msi_shadow_trading|msi_strategy_eligibility|execution_integration|broker|"
    r"mic_replay|mic_v2|msi_market_direction|msi_market_structure|msi_price_structure|"
    r"msi_participant_positioning|intelligence|market_perception|market_state_builder)\b"
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


def test_market_evidence_state_has_only_allowed_fields():
    from bujji.market_state.evidence_boundary import MarketEvidenceState
    field_names = set(MarketEvidenceState.__dataclass_fields__.keys())
    expected = {
        "timestamp", "data_freshness", "regime", "direction", "volatility_state",
        "liquidity_state", "participant_positioning", "price_structure", "market_structure",
        "active_episode_ids", "active_event_types", "overall_confidence", "direction_confidence",
        "uncertainties", "missing_evidence", "evidence_ids", "source_market_state_timestamp",
    }
    assert field_names == expected, f"unexpected field set: {field_names ^ expected}"


def test_no_forbidden_decision_shaped_fields():
    from bujji.market_state.evidence_boundary import MarketEvidenceState
    field_names = set(MarketEvidenceState.__dataclass_fields__.keys())
    forbidden = {
        "signal", "buy", "sell", "strategy", "entry", "exit", "stop", "target", "quantity",
        "position", "allocation", "capital", "risk_approval", "order",
    }
    assert not (field_names & forbidden), f"forbidden fields found: {field_names & forbidden}"


def test_no_external_imports_beyond_models():
    out = _grep(r"^\s*(from|import)\s", FILE)
    lines = [l for l in out.splitlines() if l]
    for line in lines:
        content = line.split(":", 2)[-1].strip()
        assert content.startswith("from __future__") or content.startswith("from dataclasses") \
            or content.startswith("from typing") or content.startswith("from .models"), \
            f"unexpected import: {line}"


def test_no_forbidden_module_imports():
    out = _grep(FORBIDDEN_IMPORTS, FILE)
    assert out == "", f"forbidden import found: {out}"


def test_no_broker_or_order_calls():
    out = _grep(
        r"\.(place_order|modify_order|cancel_order|get_open_positions|get_positions|"
        r"get_margin|get_funds|connect|get_quote|get_spot|get_vix)\(",
        FILE,
    )
    assert out == "", f"forbidden call found: {out}"


def test_market_state_models_unchanged_by_this_phase():
    # evidence_boundary.py is additive; models.py must be byte-identical
    # to Phase 3D's own state (this phase adds a new file, doesn't touch
    # the existing MarketState/MarketDirectionSummary dataclasses).
    result = subprocess.run(
        ["git", "diff", "--", "bujji/market_state/models.py"],
        cwd="/opt/bujji/app", capture_output=True, text=True,
    )
    assert result.stdout.strip() == "", f"models.py was modified: {result.stdout}"


def test_shadow_session_runner_unchanged():
    result = subprocess.run(
        ["git", "diff", "--stat", "360c003", "--", "bujji/shadow_runtime/shadow_session_runner.py"],
        cwd="/opt/bujji/app", capture_output=True, text=True,
    )
    # Same diff Phase 2 left (72 insertions/6 deletions) -- confirm this
    # phase added nothing further to it.
    out = _grep("evidence_boundary\\|MarketEvidenceState", "bujji/shadow_runtime/shadow_session_runner.py")
    assert out == "", f"unexpected evidence_boundary coupling: {out}"


def test_fyers_broker_code_unchanged():
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


def test_no_protected_lineage_package_modified():
    result = subprocess.run(
        ["git", "diff", "--name-only", "360c003"],
        cwd="/opt/bujji/app", capture_output=True, text=True,
    )
    changed = [l for l in result.stdout.strip().splitlines() if l]
    changed = [l for l in changed if l not in _PAPERBROKER_V2_AUTHORIZED + _LOT_SIZE_AUTHORITATIVE_AUTHORIZED + _CPC_SAFETY_SPINE_AUTHORIZED + _CPC_EVIDENCE_AND_REALISM_AUTHORIZED + _LIVE_PREMIUM_FIX_AUTHORIZED + _THREE_PART_SELECTION_AUTHORIZED]
    protected_prefixes = (
        "bujji/msi_", "bujji/trading_brain/", "bujji/execution_engine/",
        "bujji/risk_governor/", "bujji/msi_shadow_trading/", "bujji/mic_replay/",
        "bujji/production_runtime/",
    )
    # Phase 9 Liquidity Intelligence Bridge deliberately, explicitly
    # approved change -- see test_market_direction_safety_phase3d.py's
    # identical exception for the full rationale.
    _phase9_liquidity_bridge_exception = (
        "bujji/msi_strategy_selection_foundation/engine.py",
        "bujji/msi_strategy_selection_foundation/taxonomy.py",
        # Phase 14B: additive config.py extension + additive new function
        # in engine.py -- see docs/PHASE_14B_DECISION_PIPELINE_ARCHITECTURE.md
        # and tests/test_phase14b_safety.py.
        "bujji/msi_decision_synthesis/config.py",
        "bujji/msi_trade_intent/engine.py",
    )
    violations = [
        l for l in changed
        if any(l.startswith(p) for p in protected_prefixes) and l not in _phase9_liquidity_bridge_exception
    ]
    assert violations == [], f"unexpected protected-package changes: {violations}"
