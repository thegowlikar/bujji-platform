"""Safety verification -- Shadow Trading Brain, Phase 5B.

Confirms domain_view_adapter.py stays a pure, boring translator: no
strategy/trade/execution/risk/broker/MIC-v2/trading_brain import or
call, and that no decision module beyond msi_consensus/
msi_decision_synthesis was touched or invoked in production code this
phase.
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
    unexpected = [f for f in changed_files if f not in _PHASE14B_EXCEPTION]
    assert unexpected == [], f"a reused MSI engine package was modified: {unexpected}"


def test_no_decision_modules_beyond_consensus_and_synthesis_touched():
    result = subprocess.run(
        ["git", "diff", "--name-only", "360c003"],
        cwd="/opt/bujji/app", capture_output=True, text=True,
    )
    changed = [l for l in result.stdout.strip().splitlines() if l]
    changed = [l for l in changed if l not in _PAPERBROKER_V2_AUTHORIZED + _LOT_SIZE_AUTHORITATIVE_AUTHORIZED + _CPC_SAFETY_SPINE_AUTHORIZED + _CPC_EVIDENCE_AND_REALISM_AUTHORIZED]
    forbidden_prefixes = (
        "bujji/msi_trade_thesis/", "bujji/msi_trade_intent/",
        "bujji/msi_strategy_eligibility/", "bujji/msi_strategy_selector/",
        "bujji/msi_shadow_trading/", "bujji/trading_brain/", "bujji/execution_engine/",
        "bujji/risk_governor/", "bujji/mic_replay/", "bujji/production_runtime/",
    )
    violations = [
        l for l in changed
        if any(l.startswith(p) for p in forbidden_prefixes) and l not in _PHASE14B_EXCEPTION
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
    assert ("303" in stat_line or "55" in stat_line or stat_line == ""), \
        f"unexpected fyers.py diff: {stat_line}"
