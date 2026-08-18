"""Phase 17G.A — Reality Structure Bridge tests."""
import ast
import sys
import tempfile
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from bujji.historical_reality.capture import build_historical_observation
from bujji.historical_reality.store import HistoricalObservationStore
from bujji.market_observation import taxonomy as moc_taxonomy
from bujji.reality_structure_bridge.bridge import (
    REPLAY_DETECTION_CONTEXT, build_episodes_and_events, structure_as_of,
)


def _five_min_obs(instrument_identity, ts, close):
    obs = build_historical_observation(
        instrument_identity=instrument_identity, instrument_type="SPOT",
        resolution=moc_taxonomy.RESOLUTION_FIVE_MINUTE, timestamp=ts,
        payload={"open": close, "high": close, "low": close, "close": close, "volume": None},
        source="fyers_historical", access_method="direct_sdk_fyers_historical_intraday_rest",
        source_epoch=1, source_symbol=instrument_identity,
        raw_artifact_ref="x", ingestion_run_id="RUN-x", retrieved_at=ts,
        certification_status="CERTIFIED_AVAILABLE", certification_ref="ref",
    )
    return obs.observation


# --- Purity / replay-safety ---------------------------------------------------
def test_bridge_module_contains_no_wall_clock_call():
    """Mirrors the Volatility Structure Bridge's (Series 88) own
    AST-based verification technique."""
    src = (_REPO_ROOT / "bujji" / "reality_structure_bridge" / "bridge.py").read_text()
    tree = ast.parse(src)
    calls = [
        n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, "id", None)
        for n in ast.walk(tree) if isinstance(n, ast.Call)
    ]
    assert not any(c in ("now", "now_ist", "utcnow") for c in calls)


# --- Provenance honesty correction -----------------------------------------------
def test_events_are_stamped_replay_never_live():
    observations = [
        _five_min_obs("NSE:NIFTY50-INDEX", f"2026-08-01T09:{15+5*i:02d}:00+05:30", 100.0 + i)
        for i in range(5)
    ]
    _episodes, events = build_episodes_and_events(observations)
    assert events, "expected at least one PRICE_CHANGED event from a monotonic close series"
    for event in events:
        assert event.provenance.detection_context == REPLAY_DETECTION_CONTEXT
        assert event.provenance.detection_context != "LIVE"


# --- INV-8: single-instrument stream (structural, verified via usage) -----------
def test_build_episodes_processes_one_instrument_stream_only():
    """The bridge's own contract: callers pass one instrument's ordered
    observation list per call. Verified here by confirming every event
    built from a single-instrument sequence traces back to that
    sequence's own observation ids only."""
    observations = [
        _five_min_obs("NSE:NIFTY50-INDEX", f"2026-08-01T09:{15+5*i:02d}:00+05:30", 100.0 + i)
        for i in range(4)
    ]
    obs_ids = {o.identity.observation_id for o in observations}
    episodes, events = build_episodes_and_events(observations)
    for event in events:
        assert set(event.originating_observation_ids).issubset(obs_ids)
    for episode in episodes:
        assert set(episode.originating_observation_ids).issubset(obs_ids)


# --- Session-boundary closure emerges from unmodified defaults ------------------
def test_overnight_gap_closes_the_prior_session_episode():
    session_1 = [
        _five_min_obs("NSE:NIFTY50-INDEX", f"2026-08-01T09:{15+5*i:02d}:00+05:30", 100.0 + i)
        for i in range(4)
    ]
    session_2_open = _five_min_obs("NSE:NIFTY50-INDEX", "2026-08-02T09:15:00+05:30", 90.0)
    episodes, _events = build_episodes_and_events(session_1 + [session_2_open])
    # The overnight gap (>> 1800s close_after_seconds) must have closed
    # whatever episode was open at end of session 1.
    assert any(ep.current_state == "CLOSED" for ep in episodes)


# --- structure_as_of: no-lookahead, honest-empty, and a real assessment ---------
def test_structure_as_of_returns_none_for_a_date_with_insufficient_data():
    with tempfile.TemporaryDirectory() as d:
        store = HistoricalObservationStore(str(Path(d) / "hist.db"))
        price, market = structure_as_of(
            "NSE:NIFTY50-INDEX", "2026-08-01T09:15:00+05:30", historical_store=store,
        )
        assert price is None and market is None


def test_structure_as_of_never_includes_a_bar_after_as_of():
    with tempfile.TemporaryDirectory() as d:
        store = HistoricalObservationStore(str(Path(d) / "hist.db"))
        for i in range(5):
            store.write(build_historical_observation(
                instrument_identity="NSE:NIFTY50-INDEX", instrument_type="SPOT",
                resolution=moc_taxonomy.RESOLUTION_FIVE_MINUTE,
                timestamp=f"2026-08-01T09:{15+5*i:02d}:00+05:30",
                payload={"open": 100.0 + i, "high": 100.0 + i, "low": 100.0 + i,
                         "close": 100.0 + i, "volume": None},
                source="fyers_historical", access_method="direct_sdk_fyers_historical_intraday_rest",
                source_epoch=i, source_symbol="NSE:NIFTY50-INDEX",
                raw_artifact_ref="x", ingestion_run_id="RUN-x",
                retrieved_at=f"2026-08-01T09:{15+5*i:02d}:00+05:30",
                certification_status="CERTIFIED_AVAILABLE", certification_ref="ref",
            ))
        # A "future" bar the query must never see.
        store.write(build_historical_observation(
            instrument_identity="NSE:NIFTY50-INDEX", instrument_type="SPOT",
            resolution=moc_taxonomy.RESOLUTION_FIVE_MINUTE,
            timestamp="2026-08-01T10:00:00+05:30",
            payload={"open": 999.0, "high": 999.0, "low": 999.0, "close": 999.0, "volume": None},
            source="fyers_historical", access_method="direct_sdk_fyers_historical_intraday_rest",
            source_epoch=99, source_symbol="NSE:NIFTY50-INDEX",
            raw_artifact_ref="x", ingestion_run_id="RUN-x", retrieved_at="2026-08-01T10:00:00+05:30",
            certification_status="CERTIFIED_AVAILABLE", certification_ref="ref",
        ))
        price, market = structure_as_of(
            "NSE:NIFTY50-INDEX", "2026-08-01T09:35:00+05:30", historical_store=store,
        )
        assert price is not None
        assert "999.0" not in str(price.explanation)
        for obs_id in price.supporting_observation_ids:
            assert "10:00:00" not in obs_id  # weak but real: the future bar's own hash should never surface.


def test_structure_as_of_provenance_names_the_bridge():
    with tempfile.TemporaryDirectory() as d:
        store = HistoricalObservationStore(str(Path(d) / "hist.db"))
        for i in range(4):
            store.write(build_historical_observation(
                instrument_identity="NSE:NIFTY50-INDEX", instrument_type="SPOT",
                resolution=moc_taxonomy.RESOLUTION_FIVE_MINUTE,
                timestamp=f"2026-08-01T09:{15+5*i:02d}:00+05:30",
                payload={"open": 100.0 + i, "high": 100.0 + i, "low": 100.0 + i,
                         "close": 100.0 + i, "volume": None},
                source="fyers_historical", access_method="direct_sdk_fyers_historical_intraday_rest",
                source_epoch=i, source_symbol="NSE:NIFTY50-INDEX",
                raw_artifact_ref="x", ingestion_run_id="RUN-x",
                retrieved_at=f"2026-08-01T09:{15+5*i:02d}:00+05:30",
                certification_status="CERTIFIED_AVAILABLE", certification_ref="ref",
            ))
        price, market = structure_as_of(
            "NSE:NIFTY50-INDEX", "2026-08-01T09:35:00+05:30", historical_store=store,
        )
        assert price is not None
        assert "reality_structure_bridge" in price.provenance
        assert "REPLAY" in price.provenance
        assert "reality_structure_bridge" in market.provenance


def test_structure_as_of_real_2020_03_23_produces_a_coherent_assessment():
    """Real-data validation against the VPS's actual Historical Reality
    corpus, mirroring this project's standing discipline. Skips
    gracefully if the real store isn't present (e.g. CI without the
    production data directory)."""
    real_db = _REPO_ROOT / "data" / "historical_reality" / "normalized" / "historical_observations.db"
    if not real_db.exists():
        return
    store = HistoricalObservationStore(str(real_db))
    price, market = structure_as_of(
        "NSE:NIFTY50-INDEX", "2020-03-23T15:25:00+05:30", historical_store=store,
    )
    assert price is not None and market is not None
    assert price.structure_integrity == "COHERENT"
    assert len(price.supporting_observation_ids) > 0
    assert price.confidence in ("HIGH", "MODERATE", "LOW")


def test_structure_as_of_real_2018_10_26_produces_a_coherent_assessment():
    """Second real-data validation date, per this phase's own
    instruction -- a different market character (a choppy, two-way
    breakdown-then-partial-recovery session, the October 2018
    correction) than 2020-03-23's sustained crash-then-reversal, to
    confirm lookback_bars=75 doesn't only look sane on one regime.
    Skips gracefully if the real store isn't present."""
    real_db = _REPO_ROOT / "data" / "historical_reality" / "normalized" / "historical_observations.db"
    if not real_db.exists():
        return
    store = HistoricalObservationStore(str(real_db))

    price_open, market_open = structure_as_of(
        "NSE:NIFTY50-INDEX", "2018-10-26T10:00:00+05:30", historical_store=store,
    )
    price_close, market_close = structure_as_of(
        "NSE:NIFTY50-INDEX", "2018-10-26T15:25:00+05:30", historical_store=store,
    )
    for price, market in ((price_open, market_open), (price_close, market_close)):
        assert price is not None and market is not None
        assert price.structure_integrity == "COHERENT"
        assert len(price.contradictions) == 0
        assert len(price.supporting_observation_ids) > 0
        assert price.confidence in ("HIGH", "MODERATE", "LOW")

    # Real, session-specific finding this validation run surfaced: the
    # morning read shows a CONFIRMED breakdown with resistance
    # ESTABLISHED (early-session weakness); by close, breakdown has
    # softened to DEVELOPING with support ESTABLISHED (a real partial
    # recovery into the close) -- a materially different, and
    # plausible, read between the two query points, not a static or
    # degenerate output.
    assert market_open.breakdown_state == "CONFIRMED"
    assert market_close.breakdown_state == "DEVELOPING"
