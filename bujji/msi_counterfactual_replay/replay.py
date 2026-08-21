"""CRE replay — Series 102. THE ONE FILE in this package permitted to
import and invoke real Production decision functions.

This is a disclosed, narrow, independently-reviewed exception to the
rest of the package's (and every prior Series 100/101 package's) zero-
Production-imports rule -- CRE's entire purpose is to replay real,
alternative, production-valid decision paths, which structurally
requires calling the real frozen pipeline. Every other file in this
package (engine.py, models.py, journal.py, query.py) stays pure and
Production-import-free; only this module reaches into
`bujji.live_pipeline_bridge`/`bujji.live_shadow_validation` -- the exact
same real, established replay calling convention used throughout Sprints
115-122 (SessionDriver + run_full_cadence), now formalised into a tested,
importable module instead of a throwaway /tmp script.

Still permanently forbidden here, same as everywhere else in this
package: broker/execution/order-placing imports or calls. Verified by
tests/test_cre_isolation.py.
"""
from __future__ import annotations

from typing import Sequence

from bujji.live_pipeline_bridge import SessionDriver
from bujji.live_shadow_validation import run_full_cadence
from bujji.market_episode import engine as mee_engine
from bujji.market_observation import engine as moc_engine
from bujji.market_observation import taxonomy as moc_taxonomy
from bujji.live_market_events import engine as lme_engine
from bujji.msi_portfolio_construction.models import PortfolioState

from . import config as _config
from .models import ExploredPath


def _mk_observation(timestamp: str, price: float):
    """Real observation construction -- byte-identical to the convention
    already established in tests/test_live_shadow_validation.py and every
    /tmp replay script from Sprints 115-122. Not a new pattern."""
    return moc_engine.build_observation(
        observation_type=moc_taxonomy.ALL_OBSERVATION_TYPES[0], instrument="NIFTY", exchange="NSE", segment="EQ",
        timestamp=timestamp, resolution=moc_taxonomy.RESOLUTION_FIFTEEN_MINUTE, source="FYERS_REAL_INTRADAY",
        schema_version="1.0.0", value_kind=moc_taxonomy.VALUE_KIND_SCALAR, payload=price,
        completeness=1.0, freshness=0.0, confidence=1.0, missing_fields=(),
        validation_status=moc_taxonomy.VALIDATION_VALID, source_quality="HIGH",
        originating_source="FYERS_REAL_INTRADAY", acquisition_timestamp=timestamp,
        normalization_timestamp=timestamp, origin=moc_taxonomy.ORIGIN_HISTORICAL_RECONSTRUCTION, provenance_version="1.0.0",
    )


def run_real_path(
    label: str, day: str, bhav_text: str, candles: Sequence[dict], cutoff_timestamp: str, lock_path: str,
) -> ExploredPath:
    """Runs the REAL, unmodified frozen decision pipeline using ONLY real
    candles with timestamp <= cutoff_timestamp -- the causal truncation.
    Never a fabricated candle, never a future one; if no real candle
    exists at or before cutoff_timestamp, this raises rather than
    inventing one."""
    causal_candles = [c for c in candles if c["ts"] <= cutoff_timestamp]
    if not causal_candles:
        raise ValueError(f"no real candle exists at or before {cutoff_timestamp} on {day} -- cannot replay a path with no real data")

    driver = SessionDriver(lock_path=lock_path)
    driver.acquire()
    try:
        driver.load_option_chain(bhav_text, day)
        observations = [_mk_observation(c["ts"], c["close"]) for c in causal_candles]
        previous = None
        for obs in observations:
            driver.result.observations.append(obs)
            for ev in lme_engine.detect_price_change(obs, previous):
                driver.result.events.append(ev)
                driver.result.episodes = mee_engine.advance_time(driver.result.episodes, ev.timestamp, detection_context="REPLAY")
                driver.result.episodes = mee_engine.process_event(driver.result.episodes, ev, detection_context="REPLAY")
            previous = obs
        driver._today_closes = [(c["ts"], c["close"]) for c in causal_candles]
        last_ts = causal_candles[-1]["ts"]
        result = driver.run_decision_cadence(timestamp=last_ts)
        spot = next((r.underlying_price for r in driver._chain if r.underlying_price is not None), None)
        cadence = run_full_cadence(
            driver, spot=spot, day=day, portfolio=PortfolioState(), open_positions=(), timestamp=last_ts,
        )
        return ExploredPath(
            path_id=f"{label}-{day}-{cutoff_timestamp.replace(':', '')}",
            label=label, decision_timestamp=last_ts,
            thesis_type=result.thesis.thesis_type if result.thesis else None,
            selected_family=cadence.selection.selected_strategy_family,
            data_timestamps_used=tuple(c["ts"] for c in causal_candles),
            rationale=(f"real, unmodified Production pipeline replayed with candles truncated to <= {cutoff_timestamp}",),
            schema_version=_config.SCHEMA_VERSION,
        )
    finally:
        driver.release()
