"""The data-quality boundary can actually say no.

WHAT WAS MISSING. Nothing stood between market data and a trading decision.
A forensic trace of the trading runner for any quality gate returned exactly
one string, in one branch, for one condition. Meanwhile MarketDataAdapter was
ALREADY computing health_status and missing_fields on every snapshot, and
IntelligenceCycleRecorder was already recording the value under
"market_snapshot_health" -- where nothing read it. The signal existed and was
wired to a log line.

Separately, `origin` (LIVE/REPLAY/HISTORICAL_RECONSTRUCTION/SYNTHETIC) had
exactly two readers in the entire codebase: a membership check and a
re-serialization. Nothing branched on it, so nothing structurally stopped
synthetic or replayed data reaching a live decision.
"""
from __future__ import annotations

import importlib.util
import inspect
import logging
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from bujji.market_perception.models import (
    HEALTH_DEGRADED, HEALTH_OK, HEALTH_UNAVAILABLE, MarketSnapshot, OptionChainConfig,
    OptionChainSnapshot, OptionLeg, FutureSnapshot, SpotSnapshot, VixSnapshot,
)
from bujji.production_runtime.market_data_gate import (
    QUALITY_DEGRADED, QUALITY_GOOD, QUALITY_INVALID, QUALITY_UNAVAILABLE,
    REASON_EVIDENCE_TRAIL_BROKEN, assess_market_data,
)

_CFG = OptionChainConfig(**{p.name: 2 for p in inspect.signature(OptionChainConfig).parameters.values()
                            if p.default is inspect.Parameter.empty})
_LEG = OptionLeg(symbol="X", strike=24400.0, option_type="CE", ltp=120.0, bid=119.5,
                 ask=120.5, spread=1.0, volume=1200.0, open_interest=8144.0,
                 iv=None, delta=None, gamma=None, theta=None, vega=None)


def snap(health=HEALTH_OK, missing=(), source="fyers_live", spot=24416.2, chain=True):
    return MarketSnapshot(
        snapshot_version="1.0", timestamp="2026-08-20T09:21:42+05:30", source=source,
        latency_ms=812.4, health_status=health, missing_fields=tuple(missing),
        spot=SpotSnapshot(symbol="NSE:NIFTY50-INDEX", ltp=spot),
        vix=VixSnapshot(value=11.4, prev_close=11.2),
        futures=FutureSnapshot(symbol="F", ltp=24461.2, volume=1, open_interest=None,
                               basis=45.0, premium_discount=0.1),
        option_chain=(OptionChainSnapshot(underlying="NIFTY", expiry="2026-08-25",
                                          atm_strike=24400.0, config=_CFG, legs=(_LEG,))
                      if chain else None))


class TestProvenanceIsEnforcedNotJustStored:
    @pytest.mark.parametrize("source,expected", [
        ("paper_synthetic", "SYNTHETIC"),
        ("replay_chain", "REPLAY"),
        ("fyers_random_walk", "SYNTHETIC"),
        ("historical_reconstruction", "HISTORICAL_RECONSTRUCTION"),
    ])
    def test_non_live_data_cannot_reach_a_trading_decision(self, source, expected):
        v = assess_market_data(snap(source=source))
        assert v.may_trade is False
        assert v.quality == QUALITY_INVALID
        assert f"NON_LIVE_ORIGIN:{expected}" in v.reasons

    def test_live_data_passes(self):
        assert assess_market_data(snap()).may_trade is True

    def test_the_dangerous_classification_wins_a_mixed_label(self):
        """'fyers_live_replay' is a replay. A substring match that returned
        LIVE because the word appears would be the reassuring reading."""
        assert assess_market_data(snap(source="fyers_live_replay")).quality == QUALITY_INVALID

    def test_a_replay_session_may_declare_itself(self):
        """Enforcement is a policy, not a prohibition on replay existing."""
        v = assess_market_data(snap(source="replay_chain"), required_origin="REPLAY")
        assert v.may_trade is True


class TestLoadBearingDataStopsTheTrade:
    def test_no_spot_no_trade(self):
        v = assess_market_data(snap(HEALTH_UNAVAILABLE, ("spot",), spot=None))
        assert v.may_trade is False and v.quality == QUALITY_UNAVAILABLE

    def test_no_chain_no_options_trade(self):
        """There is nothing to build legs from."""
        v = assess_market_data(snap(HEALTH_DEGRADED, ("option_chain",), chain=False))
        assert v.may_trade is False and v.quality == QUALITY_INVALID

    @pytest.mark.parametrize("field", ["vix", "futures"])
    def test_non_load_bearing_absence_degrades_rather_than_stops(self, field):
        """VIX and futures inform lenses that already go honestly dark
        without them. Removing the trade would be stricter than the
        evidence warrants."""
        v = assess_market_data(snap(HEALTH_DEGRADED, (field,)))
        assert v.may_trade is True and v.quality == QUALITY_DEGRADED
        assert f"DEGRADED_MISSING:{field}" in v.reasons


class TestItFailsClosed:
    def test_no_snapshot_no_trade(self):
        assert assess_market_data(None).may_trade is False

    def test_an_unrecognised_health_status_is_refused(self):
        """A gate that permits on paths its author did not anticipate is not
        a gate.

        MarketSnapshot itself rejects an unknown health_status in
        __post_init__, so this cannot be reached through the model -- that is
        defense in depth, not redundancy. The gate must still refuse a
        snapshot-shaped object from any OTHER producer, which is exactly what
        a future adapter or a deserialized record could be.
        """
        class _Foreign:
            health_status = "SOMETHING_NEW"
            missing_fields = ()
            latency_ms = 1.0
            source = "fyers_live"

        v = assess_market_data(_Foreign())
        assert v.may_trade is False and v.quality == QUALITY_INVALID

    def test_the_model_itself_also_rejects_an_unknown_status(self):
        """The first of the two layers."""
        with pytest.raises(ValueError):
            snap(health="SOMETHING_NEW")

    def test_a_snapshot_with_no_attributes_at_all_is_refused(self):
        v = assess_market_data(object())
        assert v.may_trade is False


class TestTheEvidenceTrailIsNeverReportedGreen:
    def test_a_broken_trail_degrades_the_verdict(self):
        """GOOD while carrying EVIDENCE_TRAIL_BROKEN is exactly the
        false-reassurance shape this gate exists to remove."""
        v = assess_market_data(snap(), evidence_integrity={
            "all_cited_ids_resolve": False, "resolution_rate": 0.0})
        assert v.quality == QUALITY_DEGRADED
        assert v.quality != QUALITY_GOOD
        assert REASON_EVIDENCE_TRAIL_BROKEN in v.reasons

    def test_it_blocks_when_enforcement_is_on(self):
        v = assess_market_data(snap(), evidence_integrity={
            "all_cited_ids_resolve": False, "resolution_rate": 0.0},
            require_whole_evidence_trail=True)
        assert v.may_trade is False

    def test_a_whole_trail_stays_good(self):
        v = assess_market_data(snap(), evidence_integrity={
            "all_cited_ids_resolve": True, "resolution_rate": 1.0})
        assert v.quality == QUALITY_GOOD and v.may_trade is True

    def test_the_resolution_rate_is_carried_onto_the_verdict(self):
        v = assess_market_data(snap(), evidence_integrity={
            "all_cited_ids_resolve": True, "resolution_rate": 1.0})
        assert v.evidence_resolution_rate == 1.0


class TestTheRunnerActuallyRefuses:
    """The gate is only real if the entry path honours it."""

    @staticmethod
    def _runner_module():
        spec = importlib.util.spec_from_file_location(
            "runner_gate", REPO_ROOT / "bujji_options_os_runner.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    class _Stub:
        _logger = logging.getLogger("gate-test")

        def __init__(self, verdict, origin):
            self._data_quality = verdict
            self._intelligence_origin = origin
            self._governor_result_summary = {}
            # TWO PRECONDITIONS NOW SIT AHEAD OF THE DATA-QUALITY CLAUSE in
            # `_data_quality_permits_entry`, and this stub declares both out of
            # scope EXPLICITLY rather than either gate being loosened for it:
            #
            #   universe coverage -- the whole configured universe must be
            #     subscribed AND ticking before a strike is selected; a book
            #     that never ticked makes every later judgement rest on prices
            #     that never arrived.
            #   position truth -- an entry taken while the broker account
            #     cannot be read is unsafe however good the market data is.
            #
            # These tests exercise the DATA-QUALITY verdict only.
            self._universe = None
            self._universe_requested = ()
            self._universe_error = "NOT_APPLICABLE: stub -- universe not under test"
            self._tick_feed = None
            self._last_reconciliation = object()
            self._reconciliation_blocks_entry = False

        # Borrowed off the real class, so the stub must answer the calls that
        # gate makes.
        def _ensure_universe_subscribed(self):
            return None

        def _record_universe_coverage(self):
            return True

        def _block_entry(self, reason):
            """Mirrors the real recorder, including the ACCUMULATED key.

            A stub that wrote only `entry_blocked_by` would let these tests
            pass while the session verdict -- which grades
            `entry_blocked_reasons`, because the singular key is
            last-write-wins across up to 96 cycles -- saw nothing. The gate
            would be tested and the alarm still silent.
            """
            self._governor_result_summary["entry_blocked_by"] = reason
            recorded = self._governor_result_summary.setdefault(
                "entry_blocked_reasons", [])
            if reason not in recorded:
                recorded.append(reason)

        def _reconcile_broker_positions(self, stage_label):  # pragma: no cover
            raise AssertionError(
                "reconciliation should not be re-run: this stub already has "
                "_last_reconciliation set")

    def test_a_refusing_verdict_blocks_entry(self):
        mod = self._runner_module()
        stub = self._Stub(assess_market_data(snap(source="paper_synthetic")), "LIVE")
        assert mod.OptionsOSRunner._data_quality_permits_entry(stub) is False
        assert stub._governor_result_summary["entry_blocked_by"].startswith("DATA_QUALITY_")

    def test_a_permitting_verdict_allows_entry(self):
        mod = self._runner_module()
        stub = self._Stub(assess_market_data(snap()), "LIVE")
        assert mod.OptionsOSRunner._data_quality_permits_entry(stub) is True

    def test_a_missing_verdict_blocks_when_a_snapshot_was_expected(self):
        """An intelligence broker was built, so a snapshot should have been
        graded. Its absence means we do not know -- so we refuse."""
        mod = self._runner_module()
        stub = self._Stub(None, "LIVE")
        assert mod.OptionsOSRunner._data_quality_permits_entry(stub) is False
        assert stub._governor_result_summary["entry_blocked_by"] == "DATA_QUALITY_NOT_ASSESSED"

    def test_a_fixed_regime_config_is_not_refused_but_is_recorded(self):
        """No snapshot-deriving provider means nothing here to grade.
        Refusing would be the gate answering a question nobody asked -- but
        trading without a boundary is recorded, not passed silently."""
        mod = self._runner_module()
        stub = self._Stub(None, None)
        assert mod.OptionsOSRunner._data_quality_permits_entry(stub) is True
        assert stub._governor_result_summary["data_quality"] == "NOT_APPLICABLE"


class TestProductionConfigStillTrades:
    def test_the_live_production_regime_yields_a_live_origin(self):
        """config/options_os_paper_trading.yaml uses market_thesis_live, which
        builds a FyersBroker and sets origin LIVE. If this ever stops being
        true the gate would silently block every entry."""
        cfg = (REPO_ROOT / "config" / "options_os_paper_trading.yaml").read_text()
        assert "type: market_thesis_live" in cfg
        assert assess_market_data(snap(source="fyers_live")).may_trade is True
