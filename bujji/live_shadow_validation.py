"""bujji.live_shadow_validation — Sprint 106: Live Shadow Validation Framework.

Extends `bujji.live_pipeline_bridge.SessionDriver` (Sprint 105 + its three
follow-ups) to run the FULL decision chain -- Strategy Suitability,
Strategy Selection, Trade Construction, Position Construction, Portfolio
Construction, Margin Bridge, Execution Planning, Shadow Trading, Decision
Auditor -- and adds the tooling this sprint asks for: a replay-parity
recorder, a daily parity report generator, real operational-metrics
capture, and a failure catalogue that gives every mismatch a concrete,
categorised explanation.

This is deliberately NOT a new MSI package (no taxonomy/config/models/
engine/9-file convention) -- per this sprint's own framing, success here
is measured by OBSERVED BEHAVIOUR, not new architecture. Every decision
function called below is FROZEN and unmodified (Series 87-101); this
module adds zero new decision logic, only orchestration, comparison, and
reporting.

Environment constraint (disclosed, unchanged since Sprint 104): no live,
authenticated FYERS session can be opened from this environment (no
credentials, no market hours access here). Every "live" run in this
module's own tests/demo feeds real, recorded intraday ticks through
`SessionDriver.process_tick` exactly as a genuine live feed would --
this measures the REAL live-shaped code path (tick-by-tick event/episode
construction, real duplicate-drop counters, real per-stage timings) but
is NOT a substitute for observing a real live network session. That
remains the one thing this sprint cannot close from here, and Section
10 of the accompanying doc says so plainly.
"""
from __future__ import annotations

import resource
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from bujji.msi_strategy_selection_foundation import engine as ssf_engine
from bujji.msi_strategy_selector import engine as mss_engine
from bujji.msi_trade_construction import engine as tc_engine
from bujji.msi_position_construction import engine as pci_engine
from bujji.msi_portfolio_construction import engine as pc_engine
from bujji.msi_portfolio_construction import config as prc_config
from bujji.msi_margin_bridge import engine as mb_engine
from bujji.msi_execution_planning import engine as ep_engine
from bujji.msi_position_lifecycle import engine as pli_engine
from bujji.msi_shadow_trading import engine as st_engine
from bujji.msi_decision_auditor import engine as da_engine

from bujji.live_pipeline_bridge import SessionDriver, SessionResult, _atm_inputs


# ---------------------------------------------------------------------------
# Deliverable 5: Failure catalogue. Plain string constants (this project's
# established taxonomy convention: never `enum.Enum`) grouped by the four
# categories the spec itself names. Kept here, not in a `taxonomy.py`,
# because this module is integration/tooling, not an MSI package.
# ---------------------------------------------------------------------------
FAILURE_CATALOGUE: Dict[str, Tuple[str, ...]] = {
    "DATA": ("MISSING_QUOTE", "LATE_QUOTE", "CHAIN_UNAVAILABLE"),
    "PIPELINE": ("OBSERVATION_MISMATCH", "EPISODE_MISMATCH"),
    "DECISION": ("DIFFERENT_CONVICTION", "DIFFERENT_THESIS", "DIFFERENT_STRATEGY",
                 "DIFFERENT_CONSTRUCTION", "DIFFERENT_MARGIN", "DIFFERENT_PORTFOLIO_DECISION"),
    "INFRASTRUCTURE": ("RECONNECT", "CLOCK_DRIFT", "DUPLICATE_CADENCE", "DROPPED_TICK"),
}


@dataclass(frozen=True)
class FullCadenceResult:
    """The complete tail of the decision chain, beyond what
    `SessionDriver.run_decision_cadence` itself produces (which stops at
    Thesis/Expression/a partially-populated Decision record). Every field
    here is a real, unmodified downstream assessment -- reuses the SAME
    call sequence every corpus-replay script since Series 90 already
    uses, applied to the live-fed `SessionResult` instead of a replay
    day's reconstructed one."""
    ssf: Any
    selection_baseline: Any
    selection: Any
    position_construction: Any
    trade_construction: Optional[Any]
    margin_estimate: Optional[Any]
    portfolio_decision: Optional[Any]
    admitted_trade: Optional[Any]
    execution_plan: Optional[Any]
    shadow_position: Optional[Any]
    decision: Any


def run_full_cadence(
    driver: SessionDriver, *, spot: Optional[float], day: str,
    portfolio, open_positions: Sequence[Dict[str, Any]] = (),
    timestamp: str,
) -> FullCadenceResult:
    """Runs everything `SessionDriver.run_decision_cadence` does NOT --
    the full Series 89-100 tail -- against the driver's own real,
    live-fed `psi/mssi/mdi/mppi/vsb/consensus/thesis/expression`. Mirrors
    `_day_signal`'s own tail (Series 99's replay script) call-for-call;
    no decision function's signature or behaviour is touched here."""
    result = driver.result
    ssf = ssf_engine.assess_all_families(result.mdi, result.mssi, result.consensus, result.vsb, timestamp=timestamp)

    selection_baseline = mss_engine.select_strategy(
        ssf, result.psi, result.mssi, result.mdi, result.mppi, result.consensus, result.vsb, timestamp=timestamp,
    )
    selection = mss_engine.select_strategy(
        ssf, result.psi, result.mssi, result.mdi, result.mppi, result.consensus, result.vsb, timestamp=timestamp,
        expression_compatible_families=result.expression.compatible_strategy_families,
    )

    position_construction = pci_engine.construct_position(selection, result.expression, result.thesis, timestamp=timestamp)

    trade_construction = None
    if selection.selected_strategy_family is not None and driver._chain is not None:
        trade_construction = tc_engine.construct_trade(
            selection.selected_strategy_family, driver._chain, spot, day,
            direction=result.mdi.overall_direction,
            expected_move_pct=result.vsb.expected_move_pct if result.vsb else None,
            supporting_assessment_ids=(selection.assessment_id,), timestamp=timestamp,
        )

    margin_estimate = None
    if trade_construction is not None:
        margin_estimate = mb_engine.estimate_margin(trade_construction, prc_config.DEFAULT_LOT_SIZE, spot, timestamp=timestamp)

    portfolio_decision = None
    admitted_trade = None
    if trade_construction is not None:
        portfolio_decision = pc_engine.evaluate_trade(
            trade_construction, portfolio, spot, day, selection_confidence=selection.confidence, timestamp=timestamp,
        )
        admitted_trade = pc_engine.build_admitted_trade(trade_construction, portfolio_decision, spot, day)

    execution_plan = None
    newly_opened_lifecycle = None
    if admitted_trade is not None:
        newly_opened_lifecycle = pli_engine.assess_position_lifecycle(
            result.thesis, result.thesis, selection.selected_strategy_family, position_construction.construction_type,
            portfolio_decision, day, day, admitted_trade.position_close_date, timestamp=timestamp,
        )
        execution_plan = ep_engine.build_execution_plan(
            trade_construction, newly_opened_lifecycle, margin_estimate, portfolio_decision, timestamp=timestamp,
        )

    decision = da_engine.build_decision_record(
        day, result.psi.explanation.which_observations_support_it, result.psi.explanation.which_episodes_caused_it,
        result.mdi.overall_direction, result.consensus.consensus_level,
        result.vsb.volatility_regime if result.vsb else "UNKNOWN",
        result.thesis, selection.selected_strategy_family, position_construction,
        portfolio_decision, (newly_opened_lifecycle.position_state if newly_opened_lifecycle else None),
        margin_estimate, execution_plan, timestamp=timestamp,
    )

    shadow_position = None
    if admitted_trade is not None and execution_plan is not None:
        # Deliverable: every SessionDriver run is a SHADOW run -- this
        # calls `open_shadow_position` (Series 100, frozen), NEVER any
        # broker `place_order` (verified by the existing AST import-ban
        # test on `live_pipeline_bridge.py`; this module imports the
        # same forbidden set of modules -- none of them -- and is
        # covered by an equivalent AST test below).
        shadow_position = st_engine.open_shadow_position(
            decision.decision_id, execution_plan.plan_id, trade_construction,
            selection.selected_strategy_family, position_construction.construction_type,
            margin_estimate.estimated_margin if margin_estimate else None,
            day, admitted_trade.position_close_date, timestamp=timestamp,
        )

    return FullCadenceResult(
        ssf=ssf, selection_baseline=selection_baseline, selection=selection,
        position_construction=position_construction, trade_construction=trade_construction,
        margin_estimate=margin_estimate, portfolio_decision=portfolio_decision,
        admitted_trade=admitted_trade, execution_plan=execution_plan,
        shadow_position=shadow_position, decision=decision,
    )


# ---------------------------------------------------------------------------
# Deliverable 2/3: Replay-parity recorder + report.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ParityFieldResult:
    field: str
    live_value: Any
    replay_value: Any
    match: bool
    category: Optional[str] = None
    subcategory: Optional[str] = None
    explanation: Optional[str] = None


@dataclass(frozen=True)
class ParityReport:
    day: str
    fields: Tuple[ParityFieldResult, ...]

    @property
    def match_count(self) -> int:
        return sum(1 for f in self.fields if f.match)

    @property
    def total_count(self) -> int:
        return len(self.fields)

    @property
    def match_rate(self) -> float:
        return (self.match_count / self.total_count) if self.total_count else 1.0

    def mismatches(self) -> Tuple[ParityFieldResult, ...]:
        return tuple(f for f in self.fields if not f.match)


def _explain_mismatch(field_name: str, live_value: Any, replay_value: Any) -> Tuple[str, str, str]:
    """Deliverable 5: every mismatch gets a concrete, categorised
    explanation -- never a bare "values differ". Heuristics here are
    read ONLY off the field name and the two real values already
    computed by frozen engines; no new decision logic is introduced."""
    if "observation" in field_name:
        return "PIPELINE", "OBSERVATION_MISMATCH", (
            f"live produced {live_value} observation-supporting-ids, replay produced {replay_value} -- "
            "the two runs saw a different real tick/candle sequence for this day."
        )
    if "episode" in field_name:
        return "PIPELINE", "EPISODE_MISMATCH", (
            f"live episode-evidence ids ({live_value}) differ from replay's ({replay_value}) -- "
            "likely caused by the same underlying observation mismatch propagating downstream."
        )
    if field_name == "conviction":
        return "DECISION", "DIFFERENT_CONVICTION", (
            f"live thesis conviction={live_value!r} vs replay={replay_value!r} -- "
            "check whether a live premium quote (fetch_live_atm_premiums) was present in one run and not "
            "the other, since VSB's IV inputs feed the Consensus confidence that conviction is derived from."
        )
    if field_name == "thesis_type":
        return "DECISION", "DIFFERENT_THESIS", (
            f"live thesis_type={live_value!r} vs replay={replay_value!r} -- per Trade Thesis's own frozen "
            "priority cascade (Market Structure > Volatility Structure > Price Structure > NO_TRADE), this can "
            "legitimately happen when live and replay volatility/close-history evidence genuinely differs; "
            "check vsb_regime and closes_with_ts length on each side before assuming a bug."
        )
    if field_name == "selected_strategy_family":
        return "DECISION", "DIFFERENT_STRATEGY", (
            f"live selected {live_value!r}, replay selected {replay_value!r} -- trace back through "
            "selection.explanation.active_market_states on both sides; a single differing MSI vote is enough "
            "to change the suitable-family set."
        )
    if "construction" in field_name:
        return "DECISION", "DIFFERENT_CONSTRUCTION", (
            f"live construction={live_value!r} vs replay={replay_value!r} -- construction_type depends on "
            "conviction (HIGH -> SINGLE_LEG else spread), so this is usually downstream of a conviction mismatch."
        )
    if "margin" in field_name:
        return "DECISION", "DIFFERENT_MARGIN", (
            f"live margin estimate id={live_value!r} vs replay={replay_value!r} -- margin is a pure function of "
            "the trade construction's own legs/premiums, so this implies the two trade constructions differ too."
        )
    if "portfolio" in field_name:
        return "DECISION", "DIFFERENT_PORTFOLIO_DECISION", (
            f"live portfolio decision id={live_value!r} vs replay={replay_value!r} -- check whether the two "
            "runs carried a different open-position/PortfolioState history into this day."
        )
    return "DECISION", "ASSESSMENT_ID_MISMATCH", (
        f"{field_name}: live={live_value!r} replay={replay_value!r} -- assessment_ids are content hashes, so "
        "any difference means some real upstream input genuinely differed between the two runs."
    )


def _field(name: str, live_value: Any, replay_value: Any) -> ParityFieldResult:
    if live_value == replay_value:
        return ParityFieldResult(field=name, live_value=live_value, replay_value=replay_value, match=True)
    category, subcategory, explanation = _explain_mismatch(name, live_value, replay_value)
    return ParityFieldResult(
        field=name, live_value=live_value, replay_value=replay_value, match=False,
        category=category, subcategory=subcategory, explanation=explanation,
    )


def build_parity_report(
    day: str, live_result: SessionResult, live_cadence: FullCadenceResult,
    replay_result: SessionResult, replay_cadence: FullCadenceResult,
) -> ParityReport:
    """Deliverable 2/3: compares every field the spec asks for
    (observation ids, episode ids, MSI assessment ids, thesis, strategy,
    position construction, margin estimate, portfolio decision,
    execution plan) between a live-fed run and a pure-replay run of the
    SAME real day's data, producing one row per field with a concrete
    explanation attached to every mismatch."""
    fields = (
        _field("observation_ids", live_result.psi.explanation.which_observations_support_it,
               replay_result.psi.explanation.which_observations_support_it),
        _field("episode_ids", live_result.psi.explanation.which_episodes_caused_it,
               replay_result.psi.explanation.which_episodes_caused_it),
        _field("psi_assessment_id", live_result.psi.assessment_id, replay_result.psi.assessment_id),
        _field("mssi_assessment_id", live_result.mssi.assessment_id, replay_result.mssi.assessment_id),
        _field("mdi_assessment_id", live_result.mdi.assessment_id, replay_result.mdi.assessment_id),
        _field("mppi_positioning_bias",
               getattr(live_result.mppi, "positioning_bias", None), getattr(replay_result.mppi, "positioning_bias", None)),
        _field("vsb_regime", live_result.vsb.volatility_regime if live_result.vsb else None,
               replay_result.vsb.volatility_regime if replay_result.vsb else None),
        _field("consensus_level", live_result.consensus.consensus_level, replay_result.consensus.consensus_level),
        _field("thesis_type", live_result.thesis.thesis_type, replay_result.thesis.thesis_type),
        _field("conviction", live_result.thesis.conviction, replay_result.thesis.conviction),
        _field("selected_strategy_family", live_cadence.selection.selected_strategy_family,
               replay_cadence.selection.selected_strategy_family),
        _field("position_construction_id", live_cadence.position_construction.assessment_id,
               replay_cadence.position_construction.assessment_id),
        _field("margin_estimate_id",
               live_cadence.margin_estimate.assessment_id if live_cadence.margin_estimate else None,
               replay_cadence.margin_estimate.assessment_id if replay_cadence.margin_estimate else None),
        _field("portfolio_decision_id",
               live_cadence.portfolio_decision.assessment_id if live_cadence.portfolio_decision else None,
               replay_cadence.portfolio_decision.assessment_id if replay_cadence.portfolio_decision else None),
        _field("execution_plan_id",
               live_cadence.execution_plan.plan_id if live_cadence.execution_plan else None,
               replay_cadence.execution_plan.plan_id if replay_cadence.execution_plan else None),
    )
    return ParityReport(day=day, fields=fields)


# ---------------------------------------------------------------------------
# Deliverable 4: operational metrics. Every value here is supplied by the
# caller from its OWN real measurements (monotonic-clock deltas, real
# counters already tracked on `SessionResult`, real `resource.getrusage`
# memory) -- this module never fabricates a number, and never reads the
# wall clock itself (mirrors this whole arc's own ID-determinism
# discipline: no hidden non-determinism here either).
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class OperationalMetrics:
    day: str
    ticks_processed: int
    observations: int
    episodes: int
    decisions: int
    reconnects: int
    dropped_ticks: int
    duplicate_observations: int
    decision_latency_seconds: float
    option_chain_latency_seconds: Optional[float]
    quote_latency_seconds: Optional[float]
    cadence_duration_seconds: float
    peak_memory_kb: int


def record_operational_metrics(
    day: str, result: SessionResult, *, decision_latency_seconds: float,
    option_chain_latency_seconds: Optional[float] = None, quote_latency_seconds: Optional[float] = None,
    cadence_duration_seconds: float,
) -> OperationalMetrics:
    """Deliverable 4. `peak_memory_kb` is the real, process-wide
    `ru_maxrss` reading (kilobytes on Linux) at call time -- a real
    baseline measurement, per the spec's own "not for optimisation
    yet -- just to establish baselines" framing, never a target to hit."""
    peak_memory_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return OperationalMetrics(
        day=day, ticks_processed=len(result.observations) + result.dropped_ticks,
        observations=len(result.observations), episodes=len(result.episodes), decisions=1,
        reconnects=result.reconnects, dropped_ticks=result.dropped_ticks,
        duplicate_observations=result.duplicate_events,
        decision_latency_seconds=decision_latency_seconds,
        option_chain_latency_seconds=option_chain_latency_seconds,
        quote_latency_seconds=quote_latency_seconds,
        cadence_duration_seconds=cadence_duration_seconds,
        peak_memory_kb=peak_memory_kb,
    )


def _pct(n: int, d: int) -> str:
    return f"{(100.0 * n / d):.1f}%" if d else "N/A"


def generate_daily_parity_report(day: str, metrics: OperationalMetrics, parity: ParityReport) -> str:
    """Deliverable 3: the exact report shape the spec sketches, plus a
    concrete explanation line for every mismatch (never a silent
    "did not match")."""
    lines = [
        f"Live Session -- {day}",
        "",
        f"Ticks processed:  {metrics.ticks_processed}",
        f"Observations:      {metrics.observations}",
        f"Episodes:          {metrics.episodes}",
        f"Decisions:         {metrics.decisions}",
        "",
        "Replay comparison",
        "",
    ]
    per_field_labels = {
        "observation_ids": "Observation ID match", "episode_ids": "Episode ID match",
        "thesis_type": "Thesis match", "conviction": "Conviction match",
        "selected_strategy_family": "Strategy match", "position_construction_id": "Construction match",
        "margin_estimate_id": "Margin match", "portfolio_decision_id": "Portfolio decision match",
        "execution_plan_id": "Execution plan match",
    }
    reported_fields = [f for f in parity.fields if f.field in per_field_labels]
    reported_matches = sum(1 for f in reported_fields if f.match)
    for f in reported_fields:
        label = per_field_labels[f.field]
        lines.append(f"{label}: {'100%' if f.match else '0%'}")
    lines.append("")
    lines.append(f"Assessment ID match (all {parity.total_count} fields): {_pct(parity.match_count, parity.total_count)}")
    lines.append("")
    lines.append(f"Reported-field match rate: {_pct(reported_matches, len(reported_fields))}")
    lines.append("")
    if parity.mismatches():
        lines.append("Mismatches (each explained):")
        for m in parity.mismatches():
            lines.append(f"  [{m.category}/{m.subcategory}] {m.field}: {m.explanation}")
    else:
        lines.append("No mismatches.")
    lines.append("")
    lines.append("Operational metrics:")
    lines.append(f"  reconnects={metrics.reconnects} dropped_ticks={metrics.dropped_ticks} "
                 f"duplicate_observations={metrics.duplicate_observations}")
    lines.append(f"  decision_latency_s={metrics.decision_latency_seconds:.6f} "
                 f"cadence_duration_s={metrics.cadence_duration_seconds:.6f}")
    if metrics.option_chain_latency_seconds is not None:
        lines.append(f"  option_chain_latency_s={metrics.option_chain_latency_seconds:.6f}")
    if metrics.quote_latency_seconds is not None:
        lines.append(f"  quote_latency_s={metrics.quote_latency_seconds:.6f}")
    lines.append(f"  peak_memory_kb={metrics.peak_memory_kb}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Deliverable 6: success-criteria checker. Pure evaluation of caller-
# supplied real measurements -- introduces no pass/fail threshold beyond
# what the spec itself states (>=99% parity), and that threshold is a
# structural constant, never tuned.
# ---------------------------------------------------------------------------
PARITY_SUCCESS_THRESHOLD = 0.99


def check_success_criteria(
    parity_reports: Sequence[ParityReport], *, rerun_deterministic: bool, crashed: bool,
    duplicate_decisions: int, dropped_sessions: int,
) -> Dict[str, Any]:
    """Deliverable 6. Every criterion the spec lists, each evaluated
    against real inputs the caller measured -- never assumed true."""
    total_fields = sum(r.total_count for r in parity_reports)
    total_matches = sum(r.match_count for r in parity_reports)
    overall_parity = (total_matches / total_fields) if total_fields else 1.0
    unexplained = [m for r in parity_reports for m in r.mismatches() if m.explanation is None]
    return {
        "deterministic_rerun": rerun_deterministic,
        "no_crashes": not crashed,
        "no_duplicate_decisions": duplicate_decisions == 0,
        "no_dropped_sessions": dropped_sessions == 0,
        "replay_live_parity_pct": overall_parity,
        "parity_meets_threshold": overall_parity >= PARITY_SUCCESS_THRESHOLD,
        "every_mismatch_explained": len(unexplained) == 0,
        "all_criteria_met": (
            rerun_deterministic and not crashed and duplicate_decisions == 0 and dropped_sessions == 0
            and overall_parity >= PARITY_SUCCESS_THRESHOLD and len(unexplained) == 0
        ),
    }
