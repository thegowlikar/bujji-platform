"""BUJJI Options OS -- Intelligence Adapter Qualification Sessions.

Operates the EXISTING Intelligence Adapter (Integration Series 1,
Sprint 1) plus the EXISTING live decision path (bujji.replay.engine.
ReplayEngine, the same Orchestrator that runs live) only. No new code.

For each of 10 sessions: run the same candle scenario through the real
decision pipeline TWICE -- once with the adapter disabled, once
enabled, both against MIC v2's real published Consumer Journal -- and
confirm the trading outcome is byte-identical except for the additive
`intelligence_reference` field on the decision journal row.
"""
import sys
sys.path.insert(0, "/opt/bujji/app")
sys.path.insert(0, "/opt/bujji/app/tests")

import asyncio
import json
import logging
from datetime import time as dtime
from pathlib import Path

from bujji.core.config import AppConfig
from bujji.replay.engine import ReplayEngine
from tests.conftest import c

CAMPAIGN_DIR = Path("/opt/bujji/app/qualification/intelligence_adapter_sessions")
EVIDENCE_LOG = CAMPAIGN_DIR / "evidence_log.jsonl"
SESSION_COUNT = 10
REAL_CONSUMER_JOURNAL = Path("/opt/bujji-mic-v2/qualification_campaign_1/consumer_journal.jsonl")

logger = logging.getLogger("intelligence_adapter_qualification")
logger.addHandler(logging.NullHandler())


def timed_config():
    cfg = AppConfig()
    cfg.timing.orb_start = dtime(9, 15)
    cfg.timing.orb_end = dtime(9, 20)
    cfg.timing.trading_start = dtime(9, 20)
    cfg.timing.trading_end = dtime(15, 15)
    cfg.timing.hard_exit = dtime(15, 5)
    return cfg


def make_candles(session_num):
    # Same validated shape as Sprint 1's own smoke validation, offset by
    # session_num*10 points so each session is numerically distinct
    # while preserving the ORB-breakout + hard-exit conditions already
    # proven to trigger a clean entry and exit.
    base = 22000 + session_num * 10
    return [
        c(9, 20, base, base + 10, base - 10, base + 5, vol=1000),
        c(9, 25, base, base + 40, base - 10, base + 35, vol=1200),
        c(15, 5, base, base + 10, base - 10, base + 5, vol=1000),
    ]


def build_cfg(base_cfg, tmp_dir, enable_adapter):
    cfg = base_cfg
    cfg.paths.journal_csv = tmp_dir / "j.csv"
    cfg.paths.database = tmp_dir / "b.db"
    cfg.paths.state_file = tmp_dir / "s.json"
    cfg.paths.decision_journal = tmp_dir / "decision_journal.jsonl"
    cfg.paths.ops_restart_count = tmp_dir / "ops_restart_count.json"
    cfg.paths.incident_log = tmp_dir / "incident_log.jsonl"
    cfg.broker.order_timeout_seconds = 0.05
    cfg.broker.poll_interval_seconds = 0.01
    cfg.intelligence_adapter.enabled = enable_adapter
    cfg.intelligence_adapter.consumer_journal_path = REAL_CONSUMER_JOURNAL
    return cfg


async def run_session(session_num):
    session_dir = CAMPAIGN_DIR / f"session_{session_num:02d}"
    dir_off = session_dir / "off"
    dir_on = session_dir / "on"
    dir_off.mkdir(parents=True, exist_ok=True)
    dir_on.mkdir(parents=True, exist_ok=True)

    candles = make_candles(session_num)

    cfg_off = build_cfg(timed_config(), dir_off, enable_adapter=False)
    cfg_on = build_cfg(timed_config(), dir_on, enable_adapter=True)

    result_off = await ReplayEngine(cfg_off, logger).run(candles)
    result_on = await ReplayEngine(cfg_on, logger).run(candles)

    anomalies = []

    if result_off.final_state != result_on.final_state:
        anomalies.append(f"final_state differs: off={result_off.final_state} on={result_on.final_state}")
    if result_off.trades != result_on.trades:
        anomalies.append("trades list differs between disabled and enabled runs")

    csv_off = cfg_off.paths.journal_csv.read_text() if cfg_off.paths.journal_csv.exists() else ""
    csv_on = cfg_on.paths.journal_csv.read_text() if cfg_on.paths.journal_csv.exists() else ""
    if csv_off != csv_on:
        anomalies.append("trade journal CSV differs between disabled and enabled runs")

    rows_off = [json.loads(l) for l in cfg_off.paths.decision_journal.read_text().splitlines()] if cfg_off.paths.decision_journal.exists() else []
    rows_on = [json.loads(l) for l in cfg_on.paths.decision_journal.read_text().splitlines()] if cfg_on.paths.decision_journal.exists() else []
    if len(rows_off) != len(rows_on):
        anomalies.append(f"decision journal row count differs: off={len(rows_off)} on={len(rows_on)}")

    intelligence_reference = None
    for row_off, row_on in zip(rows_off, rows_on):
        row_on_stripped = dict(row_on)
        intelligence_reference = row_on_stripped.pop("intelligence_reference", None)
        if "intelligence_reference" in row_off:
            anomalies.append("disabled run unexpectedly wrote an intelligence_reference")
        if row_off != row_on_stripped:
            anomalies.append("decision journal row differs beyond intelligence_reference")
        if intelligence_reference is not None and set(intelligence_reference.keys()) != {"snapshot_id", "publication_id", "replay_id"}:
            anomalies.append(f"intelligence_reference has unexpected keys: {list(intelligence_reference.keys())}")

    if not rows_off:
        anomalies.append("no decision journal rows produced -- session never entered a trade")

    root_cause_notes = []
    if anomalies:
        root_cause_notes.append("see anomalies field")
    else:
        root_cause_notes.append("clean session: trading outcome byte-identical with adapter disabled/enabled; "
                                "only the additive intelligence_reference field differed")

    return {
        "session": session_num,
        "final_state_off": result_off.final_state,
        "final_state_on": result_on.final_state,
        "trade_count_off": len(result_off.trades),
        "trade_count_on": len(result_on.trades),
        "decision_rows": len(rows_off),
        "intelligence_reference": intelligence_reference,
        "anomalies": anomalies,
        "root_cause_notes": root_cause_notes,
    }


async def main():
    if not REAL_CONSUMER_JOURNAL.exists():
        print(f"WARNING: real consumer journal not found at {REAL_CONSUMER_JOURNAL}")

    evidence_records = []
    for i in range(1, SESSION_COUNT + 1):
        evidence = await run_session(i)
        evidence_records.append(evidence)
        print(f"session {i}: final_state off/on={evidence['final_state_off']}/{evidence['final_state_on']} "
              f"trades off/on={evidence['trade_count_off']}/{evidence['trade_count_on']} "
              f"snapshot={evidence['intelligence_reference']['snapshot_id'] if evidence['intelligence_reference'] else None} "
              f"anomalies={evidence['anomalies']}")

    # Determinism check: re-run session 1 (enabled) independently, confirm identical snapshot reference.
    session1_repeat = await run_session(1)
    determinism_result = {
        "original_snapshot": evidence_records[0]["intelligence_reference"],
        "rerun_snapshot": session1_repeat["intelligence_reference"],
        "identical": evidence_records[0]["intelligence_reference"] == session1_repeat["intelligence_reference"],
    }
    print("determinism check (session 1 re-run):", determinism_result)

    with open(EVIDENCE_LOG, "w") as fh:
        for e in evidence_records:
            fh.write(json.dumps(e) + "\n")
        fh.write(json.dumps({"determinism_check": determinism_result}) + "\n")

    print("evidence records written:", len(evidence_records))
    total_anomalies = sum(len(e["anomalies"]) for e in evidence_records)
    print("total anomalies across all sessions:", total_anomalies)


if __name__ == "__main__":
    asyncio.run(main())
