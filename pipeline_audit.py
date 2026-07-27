"""BUJJI Options OS -- Decision Pipeline Refactor Series Audit (Sprint 8).

Standalone tooling, not part of the application. Verifies the
architecture Sprints 1-7 built still holds, measures stage()
instrumentation overhead, and runs a consolidated battery of replay
scenarios. Produces pipeline_audit_report.json.
"""
import ast
import asyncio
import json
import time
from pathlib import Path

import sys
sys.path.insert(0, "/opt/bujji/app")

from bujji.core.config import AppConfig
from bujji.core.logging_setup import setup_logging
from bujji.core.pipeline_stages import stage
from bujji.replay.engine import ReplayEngine
from tests.conftest import c

CORE = Path("/opt/bujji/app/bujji/core")
report = {}


# ---------------------------------------------------------------------- #
# 1. Architecture audit
# ---------------------------------------------------------------------- #
def audit_architecture():
    findings = {"stage_boundaries_found": [], "issues": []}

    orch_text = (CORE / "orchestrator.py").read_text()
    tree = ast.parse(orch_text)

    expected_stage_names = {
        "market_observation", "market_intelligence", "strategy_evaluation",
        "risk_validation", "execution_decision", "journal_recording_decision",
        "order_dispatch", "journal_recording_trade",
    }
    found_stage_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.With):
            for item in node.items:
                call = item.context_expr
                if isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id == "stage":
                    if len(call.args) >= 2 and isinstance(call.args[1], ast.Constant):
                        found_stage_names.add(call.args[1].value)

    findings["stage_boundaries_found"] = sorted(found_stage_names)
    missing = expected_stage_names - found_stage_names
    if missing:
        findings["issues"].append(f"missing documented stage boundaries: {sorted(missing)}")
    extra = found_stage_names - expected_stage_names
    if extra:
        findings["issues"].append(f"undocumented new stage boundaries: {sorted(extra)}")

    # stage() must still never swallow an exception -- verify the except
    # block ends in a bare `raise`.
    stages_text = (CORE / "pipeline_stages.py").read_text()
    stages_tree = ast.parse(stages_text)
    reraises = False
    for node in ast.walk(stages_tree):
        if isinstance(node, ast.ExceptHandler):
            if any(isinstance(n, ast.Raise) and n.exc is None for n in ast.walk(node)):
                reraises = True
    if not reraises:
        findings["issues"].append("stage()'s except block no longer re-raises unmodified -- REGRESSION")

    findings["exception_never_swallowed"] = reraises
    findings["all_documented_stages_present"] = not missing
    findings["no_undocumented_stages"] = not extra
    return findings


# ---------------------------------------------------------------------- #
# 2. Stage instrumentation overhead
# ---------------------------------------------------------------------- #
def measure_overhead():
    import logging
    logger = logging.getLogger("overhead_probe")
    logger.disabled = True  # Isolate stage() call overhead from log I/O.

    n = 10_000
    start = time.perf_counter()
    for _ in range(n):
        with stage(logger, "overhead_probe"):
            pass
    stage_elapsed = time.perf_counter() - start

    start = time.perf_counter()
    for _ in range(n):
        pass
    baseline_elapsed = time.perf_counter() - start

    per_call_overhead_us = ((stage_elapsed - baseline_elapsed) / n) * 1_000_000
    candle_interval_seconds = 5 * 60  # Real production candle_minutes=5 default.
    return {
        "iterations": n,
        "per_stage_call_overhead_microseconds": round(per_call_overhead_us, 3),
        "seven_stages_per_candle_overhead_microseconds": round(per_call_overhead_us * 7, 3),
        "candle_interval_seconds": candle_interval_seconds,
        "overhead_as_pct_of_candle_interval": round(
            (per_call_overhead_us * 7 / 1_000_000) / candle_interval_seconds * 100, 8,
        ),
    }


# ---------------------------------------------------------------------- #
# 3. Consolidated replay scenario battery
# ---------------------------------------------------------------------- #
async def run_scenarios():
    results = {}

    async def _run(name, configure, candles, post=None):
        config = AppConfig.load("/opt/bujji/app/config/config.yaml")
        workdir = f"/tmp/pipeline_audit_{name}"
        Path(workdir).mkdir(exist_ok=True)
        config.paths.journal_csv = f"{workdir}/j.csv"
        config.paths.database = f"{workdir}/b.db"
        config.paths.state_file = f"{workdir}/s.json"
        config.paths.decision_journal = f"{workdir}/decision_journal.jsonl"
        config.paths.lock_file = f"{workdir}/bujji.lock"
        config.broker.order_timeout_seconds = 0.05
        config.broker.poll_interval_seconds = 0.01
        configure(config)
        logger = setup_logging(f"{workdir}/logs", "INFO")
        engine = ReplayEngine(config, logger)
        result = await engine.run(candles)
        if post:
            await post(engine)
        results[name] = {
            "final_state": str(engine.orchestrator.state),
            "trade_count": len(result.trades),
        }

    await _run(
        "normal_exit", lambda cfg: None,
        [c(9, 20, 22000, 22010, 21990, 22005, vol=1000), c(9, 30, 22008, 22020, 22000, 22012, vol=1200)],
    )

    async def _eod(engine):
        if engine.orchestrator.has_open_position():
            await engine.orchestrator.end_of_day()
    await _run(
        "eod_square_off", lambda cfg: None,
        [c(9, 20, 22000, 22010, 21990, 22005, vol=1000), c(9, 25, 22000, 22010, 21990, 22005, vol=1000)],
        post=_eod,
    )

    await _run(
        "max_trades_gate", lambda cfg: setattr(cfg.strategy, "max_trades_per_day", 0),
        [c(9, 20, 22000, 22010, 21990, 22005, vol=1000)],
    )

    async def _run_clock_untrusted():
        config = AppConfig.load("/opt/bujji/app/config/config.yaml")
        workdir = "/tmp/pipeline_audit_clock_untrusted"
        Path(workdir).mkdir(exist_ok=True)
        config.paths.journal_csv = f"{workdir}/j.csv"
        config.paths.database = f"{workdir}/b.db"
        config.paths.state_file = f"{workdir}/s.json"
        config.paths.decision_journal = f"{workdir}/decision_journal.jsonl"
        config.paths.lock_file = f"{workdir}/bujji.lock"
        config.broker.order_timeout_seconds = 0.05
        config.broker.poll_interval_seconds = 0.01
        logger = setup_logging(f"{workdir}/logs", "INFO")
        engine = ReplayEngine(config, logger)
        engine.orchestrator.set_clock_trust(False, "audit_probe")
        result = await engine.run([c(9, 20, 22000, 22010, 21990, 22005, vol=1000)])
        results["clock_untrusted_gate"] = {
            "final_state": str(engine.orchestrator.state), "trade_count": len(result.trades),
        }
    await _run_clock_untrusted()

    await _run(
        "capital_rejection", lambda cfg: setattr(cfg.risk, "lots", 0),
        [c(9, 20, 22000, 22010, 21990, 22005, vol=1000)],
    )

    return results


def main():
    report["architecture_audit"] = audit_architecture()
    report["stage_overhead"] = measure_overhead()
    report["replay_scenarios"] = asyncio.run(run_scenarios())

    Path("/opt/bujji/app/pipeline_audit_report.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
