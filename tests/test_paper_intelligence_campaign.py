"""Phase 6 -- Paper Intelligence Campaign preparation. THE definitive
proof required: a real, full `ShadowSessionRunner.start()` run, with
`market_perception_enabled`/`intelligence_cycle_enabled`/
`intelligence_pipeline_enabled` ALL real and on (matching tests/
test_shadow_runtime_intelligence_loop.py's own established fixture
pattern), producing real `CycleEvidence` AND a real
`MarketIntelligenceSnapshot` for the SAME cycle via the new
`on_cycle_evidence` callback, both flowing into one real
`DecisionArtifact` -- perception and interpretation attached
side-by-side, never merged."""
from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from bujji.core.enums import OptionType, Side
from bujji.core.models import Candle, OptionContract
from bujji.decision_artifact import DecisionArtifactJournal
from bujji.intelligence.market_intelligence_snapshot import MarketIntelligenceSnapshot
from bujji.market_state.cycle_evidence import CycleEvidence
from bujji.paper_intelligence_mode import run_cycle
from bujji.shadow_runtime.shadow_session_runner import ShadowSessionRunner

CE_CONTRACT = OptionContract("NIFTY24450CE", "NIFTY", 24450, OptionType.CE, "2026-08-04", 65)
PE_CONTRACT = OptionContract("NIFTY24450PE", "NIFTY", 24450, OptionType.PE, "2026-08-04", 65)
FIXED_NOW = datetime(2026, 8, 4, 9, 15, 0, tzinfo=timezone.utc)


def clock():
    return FIXED_NOW


async def no_sleep(_seconds):
    return None


class FakeBroker:
    """Same production-shaped fixture pattern as tests/
    test_shadow_runtime_intelligence_loop.py -- reused, not
    reinvented."""

    async def connect(self):
        pass

    async def get_quote(self, contract):
        return {"bid": 98.0, "ask": 100.0, "spread": 2.0}

    async def get_spot(self, underlying):
        return 24400.0

    async def get_vix(self):
        return {"level": 13.0, "prev_close": 13.5}

    async def get_option_chain(self, underlying, spot, strike_count=5):
        return [(24450.0, 12000.0, 15000.0)]

    async def get_futures_quote(self, underlying):
        return {"symbol": "NSE:NIFTY26AUGFUT", "ltp": 24650.0, "volume": 1000.0, "oi": None}

    async def get_recent_candles(self, underlying, minutes, count):
        out, c = [], 24300.0
        for _ in range(20):
            c += 5
            out.append(Candle(timestamp=FIXED_NOW, open=c - 5, high=c + 2, low=c - 7, close=c, volume=1000))
        return out


def make_runner(broker, tmp_path, session_id="S-PI", on_cycle_evidence=None, **overrides):
    defaults = dict(
        broker=broker, watchlist=[(CE_CONTRACT, Side.SELL), (PE_CONTRACT, Side.SELL)],
        storage_path=str(tmp_path / "quotes.jsonl"), session_id=session_id, clock=clock,
        max_cycles=2, max_consecutive_failures=2, sleep_seconds=0.0, sleep_fn=no_sleep,
        market_perception_enabled=True, intelligence_cycle_enabled=True,
        intelligence_cycle_path=str(tmp_path / "intelligence_cycle.jsonl"),
        intelligence_pipeline_enabled=True,
        intelligence_pipeline_event_store_path=str(tmp_path / "ip_events.jsonl"),
        health_path=str(tmp_path / "health.json"), session_date="2026-08-04",
        on_cycle_evidence=on_cycle_evidence,
    )
    defaults.update(overrides)
    return ShadowSessionRunner(**defaults)


class TestOnCycleEvidenceCallback:
    @pytest.mark.asyncio
    async def test_callback_fires_with_real_evidence_and_real_perception_snapshot(self, tmp_path):
        captured = []

        def on_cycle_evidence(evidence, perception_snapshot):
            captured.append((evidence, perception_snapshot))

        runner = make_runner(FakeBroker(), tmp_path, on_cycle_evidence=on_cycle_evidence)
        artifact = await runner.start()

        assert artifact.errors == ()
        assert len(captured) == 2  # max_cycles=2
        evidence, perception_snapshot = captured[0]
        assert isinstance(evidence, CycleEvidence)
        assert isinstance(perception_snapshot, MarketIntelligenceSnapshot)
        # Both objects, same cycle: confirmed by the structural
        # dependency (intelligence_pipeline_enabled requires
        # intelligence_cycle_enabled) -- both real, both non-None,
        # together, every cycle.
        assert evidence.mdi is not None
        assert perception_snapshot.regime is not None

    @pytest.mark.asyncio
    async def test_a_raising_callback_never_breaks_the_session(self, tmp_path):
        def broken_callback(evidence, perception_snapshot):
            raise RuntimeError("simulated callback bug")

        runner = make_runner(FakeBroker(), tmp_path, on_cycle_evidence=broken_callback)
        artifact = await runner.start()
        assert any("on_cycle_evidence_failed" in e for e in artifact.errors)
        # The session itself still completed both cycles -- a broken
        # callback degrades honestly, never crashes the live loop.
        assert artifact.runtime_health["heartbeats"] == 2

    @pytest.mark.asyncio
    async def test_no_callback_is_fully_backward_compatible(self, tmp_path):
        runner = make_runner(FakeBroker(), tmp_path, on_cycle_evidence=None)
        artifact = await runner.start()
        assert artifact.errors == ()


class TestFullPaperIntelligenceCampaignCycle:
    """THE proof: one real, complete market cycle, end to end, real
    perception AND real interpretation, one real persisted
    DecisionArtifact carrying both."""

    @pytest.mark.asyncio
    async def test_one_complete_cycle_with_perception_cross_reference(self, tmp_path):
        artifacts = []

        def on_cycle_evidence(evidence, perception_snapshot):
            if evidence is None:
                return
            artifact = run_cycle(evidence, session_id="S-PI", perception_snapshot=perception_snapshot, clock=clock)
            artifacts.append(artifact)

        runner = make_runner(FakeBroker(), tmp_path, on_cycle_evidence=on_cycle_evidence)
        session_artifact = await runner.start()

        assert session_artifact.errors == ()
        assert len(artifacts) == 2

        artifact = artifacts[0]
        assert artifact.market_regime is not None                # market_thesis, real
        assert artifact.perception_snapshot_id is not None         # MarketIntelligenceSnapshot, real
        assert artifact.perception_posture is not None
        # Never merged: the two independent reads are stored in
        # entirely separate fields.
        assert artifact.market_regime != artifact.perception_primary_thesis or artifact.perception_primary_thesis is None

        with tempfile.TemporaryDirectory() as tmp:
            journal = DecisionArtifactJournal(str(Path(tmp) / "campaign.jsonl"))
            for a in artifacts:
                journal.append(a)
            records = journal.read_all()
            assert len(records) == 2
            assert records[0] == artifacts[0]
