"""The learning loop, closed at both ends -- and kept out of the real store.

TWO AUDIT FINDINGS, 2026-08-20.

(1) THE LOOP WAS OPEN AT THE READ END. D-8 made every closed position durable,
    and `governor_context_builder` asks the AdaptiveRiskMemory real questions
    on every entry decision (`all_entries()`, `lookup(strategy_type=...)`).
    But the runner handed it `AdaptiveRiskMemory()` -- fresh and empty, every
    session -- and nothing in the codebase ever called `append_observation`.
    Bujji wrote down every outcome and never opened the book.

(2) TESTS WERE WRITING INTO THE REAL DURABLE STORE. `_outcome_memory_store()`
    defaulted to `data/outcome_memory_events.jsonl` under REPO_ROOT no matter
    where the session's other artifacts went. Tests sandbox `journal_path` and
    `shadow_sessions_root` but had no reason to know about a third path, so
    from the moment D-8 began writing, every test that closed a position wrote
    to production. 78 synthetic records had accumulated (sessions
    OUTCOME-1/2/6, family SHORT_STRANGLE -- not even in SUPPORTED_FAMILIES),
    and they would have been the first thing the newly-hydrated risk memory
    learned from.
"""
from __future__ import annotations

import datetime
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from bujji.msi_trade_construction import taxonomy as mtc
from bujji.outcome_memory.models import OutcomeMemoryRecord
from bujji.production_runtime.outcome_memory_writer import persist_outcome_memory
from bujji.production_runtime.risk_memory_bridge import (
    hydrate_risk_memory, risk_entry_from_outcome)
from bujji.state_persistence.store import EventStore

CLOCK = lambda: datetime.datetime(2026, 8, 20, 10, 0)  # noqa: E731


def _record(memory_id="MEM-1", family="IRON_CONDOR", outcome="PROFIT",
            regime="SIDEWAYS", pnl=1234.5):
    return OutcomeMemoryRecord(
        memory_id=memory_id, session_id="S", position_id="P", candidate_id="C",
        strategy_family=family, entry_timestamp="2026-08-20T09:30:00+05:30",
        exit_timestamp="2026-08-20T15:15:00+05:30", recorded_at="2026-08-20T15:15:00+05:30",
        entry_regime=regime, entry_direction="NEUTRAL", underlying_symbol="NIFTY",
        outcome_direction=outcome, realized_pnl=pnl, pnl_status="KNOWN",
        final_thesis_status="INTACT", primary_cause="THETA_DECAY",
        management_status="NOT_APPLICABLE", greeks_status="NOT_AVAILABLE",
        premium_behaviour_status="NOT_AVAILABLE", portfolio_context_status="NOT_AVAILABLE",
        lifecycle_snapshot={}, attribution_snapshot={}, portfolio_context_snapshot=None)


class TestTheBridgeTranslatesHonestly:
    def test_a_closed_position_becomes_a_risk_memory_entry(self):
        e = risk_entry_from_outcome(_record(), clock=CLOCK)
        assert e is not None
        assert e.entry_id == "MEM-1" and e.strategy_type == "IRON_CONDOR"
        assert e.market_regime == "SIDEWAYS"

    @pytest.mark.parametrize("direction,expected", [
        ("PROFIT", "WIN"), ("LOSS", "LOSS"), ("BREAKEVEN", "BREAKEVEN"),
        ("UNKNOWN", "UNKNOWN"),
    ])
    def test_the_outcome_vocabularies_are_mapped_explicitly(self, direction, expected):
        """attribution says PROFIT, the risk memory says WIN. Mapped by table
        so a new value on either side becomes UNKNOWN rather than a win."""
        entry = risk_entry_from_outcome(_record(outcome=direction), clock=CLOCK)
        assert entry.realized_outcome == expected

    def test_an_unmapped_outcome_degrades_to_unknown_and_is_named(self):
        e = risk_entry_from_outcome(_record(outcome="SOMETHING_NEW"), clock=CLOCK)
        assert e.realized_outcome == "UNKNOWN"
        assert any("SOMETHING_NEW" in n for n in e.notes)

    def test_volatility_regime_is_recorded_unknown_not_back_filled(self):
        """The record does not carry it, and today's volatility is not this
        trade's. Guessing would poison the very lookups the risk chain runs."""
        e = risk_entry_from_outcome(_record(), clock=CLOCK)
        assert e.volatility_regime == "UNKNOWN"
        assert any("volatility_regime unavailable" in n for n in e.notes)

    def test_excursions_stay_none_rather_than_derived_from_pnl(self):
        """mfe/mae describe how a trade BEHAVED; realized_pnl describes how it
        ended. Substituting one for the other would be an invention."""
        e = risk_entry_from_outcome(_record(pnl=99999.0), clock=CLOCK)
        assert e.max_drawdown is None and e.max_profit is None

    def test_an_unattributable_record_is_refused(self):
        """No strategy family means every lookup it lands in is polluted."""
        assert risk_entry_from_outcome(_record(family=None), clock=CLOCK) is None


class TestHydration:
    def test_records_written_in_one_session_are_read_back_in_the_next(self, tmp_path):
        path = str(tmp_path / "outcome_memory_events.jsonl")
        for i in range(3):
            persist_outcome_memory(EventStore(path), _record(memory_id=f"M{i}"),
                                   session_id=f"DAY-{i}", recorded_at="T")
        memory, report = hydrate_risk_memory(path, clock=CLOCK)
        assert report.entries_loaded == 3 and len(memory) == 3
        assert len(memory.lookup_by_strategy("IRON_CONDOR")) == 3

    def test_an_empty_store_yields_an_empty_memory_and_says_so(self, tmp_path):
        memory, report = hydrate_risk_memory(str(tmp_path / "nothing.jsonl"), clock=CLOCK)
        assert len(memory) == 0 and report.entries_loaded == 0 and report.status == "OK"

    def test_an_unreadable_store_degrades_to_the_old_behaviour(self, tmp_path):
        """A broken memory must never stop a trading session -- it must give
        back exactly the empty memory Bujji had before this existed."""
        bad = tmp_path / "dir_not_a_file"
        bad.mkdir()
        memory, report = hydrate_risk_memory(str(bad), clock=CLOCK)
        assert len(memory) == 0
        assert report.status.startswith("UNAVAILABLE") and report.reason

    def test_the_report_accounts_for_every_record(self, tmp_path):
        path = str(tmp_path / "m.jsonl")
        persist_outcome_memory(EventStore(path), _record(memory_id="GOOD"),
                               session_id="S", recorded_at="T")
        memory, report = hydrate_risk_memory(path, clock=CLOCK)
        assert report.records_seen == report.entries_loaded + report.records_skipped


def _runner_module(name):
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        name, REPO_ROOT / "bujji_options_os_runner.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestTestsCannotTouchTheRealStore:
    def test_the_store_path_follows_the_journal_into_a_sandbox(self, tmp_path):
        """The structural fix. A caller that sandboxes its artifacts sandboxes
        this too, automatically -- no test needs to know the path exists."""
        mod = _runner_module("runner_store")

        class _Stub:
            _config = {"artifacts": {"journal_path": str(tmp_path / "sub" / "j.db")}}

        store = mod.OptionsOSRunner._outcome_memory_store(_Stub())
        assert str(tmp_path) in store.path
        assert "outcome_memory_events.jsonl" in store.path

    def test_production_defaults_still_resolve_to_the_data_directory(self):
        """The fix must not move the real store."""
        mod = _runner_module("runner_store2")

        class _Stub:
            _config = {}

        store = mod.OptionsOSRunner._outcome_memory_store(_Stub())
        assert store.path.endswith("data/outcome_memory_events.jsonl")

    def test_the_real_store_holds_no_synthetic_test_sessions(self):
        """Regression guard for the 78 records that had accumulated. Real
        sessions are OPTIONS_OS_<date>_<hex>; anything else is a test that
        escaped its sandbox."""
        path = REPO_ROOT / "data" / "outcome_memory_events.jsonl"
        if not path.exists():
            pytest.skip("no durable outcomes yet -- Bujji has not closed a position")
        offenders = []
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            sid = json.loads(line).get("session_id", "")
            if not str(sid).startswith("OPTIONS_OS_"):
                offenders.append(sid)
        assert not offenders, f"synthetic sessions in the real store: {sorted(set(offenders))[:5]}"

    def test_the_real_store_holds_no_unbuildable_families(self):
        """SHORT_STRANGLE is not in SUPPORTED_FAMILIES -- its presence was the
        tell that these records never came from a real session."""
        path = REPO_ROOT / "data" / "outcome_memory_events.jsonl"
        if not path.exists():
            pytest.skip("no durable outcomes yet")
        bad = []
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            fam = ((json.loads(line).get("payload") or {}).get("record") or {}).get("strategy_family")
            if fam and fam not in mtc.SUPPORTED_FAMILIES:
                bad.append(fam)
        assert not bad, f"families no engine can build: {sorted(set(bad))}"
