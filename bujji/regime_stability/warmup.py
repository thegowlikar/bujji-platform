"""Poll live spot often enough for the price-structure domains to see.

WHY SPOT-ONLY. The accumulating path reads exactly three things from a
MarketSnapshot -- `spot.ltp`, `spot.symbol`, `timestamp`. VIX, futures and
the option chain are never dereferenced by it, and MPPI/VSB/liquidity are
recomputed per-cycle from the CURRENT snapshot rather than accumulated. So
a warm-up observation costs ONE `/quotes` call, not the 85 that a full
`build_snapshot()` costs. Four polls is ~4 calls, not ~340.

NOTHING IS FABRICATED. Each poll is one real FYERS LTP at a real moment,
stamped with that moment. The absent legs are DECLARED, not invented:
`vix=VixSnapshot(None)`, `futures=None`, `option_chain=None`, and
`missing_fields` naming all three, with `health_status=DEGRADED` so no
downstream reader can mistake a warm-up snapshot for a full one. A poll
that returns no price yields no snapshot at all rather than a placeholder.

WHY THE COUNT IS VALIDATED UP FRONT. The stability gate subsamples by
stride, so the widest stride must still land at least
MIN_OBSERVATIONS_PER_STRIDE observations. A config that cannot satisfy
that is a configuration error, and it is far better to say so at startup
than to poll for eight minutes and then discover the gate must refuse for
a reason that had nothing to do with the market.
"""
from __future__ import annotations

import time as _time
from dataclasses import dataclass
from typing import Callable, List, Optional, Sequence

from bujji.market_perception.models import (
    HEALTH_DEGRADED, MarketSnapshot, SpotSnapshot, VixSnapshot,
)
from bujji.regime_stability import MIN_OBSERVATIONS_PER_STRIDE

WARMUP_SOURCE = "fyers_live_warmup"
SNAPSHOT_VERSION = "1.0"
_ABSENT = ("vix", "futures", "option_chain")


class WarmupConfigurationError(ValueError):
    """The poll plan cannot satisfy the stability gate."""


@dataclass(frozen=True)
class WarmupPlan:
    polls: int
    interval_seconds: float
    strides: Sequence[int]

    @property
    def duration_seconds(self) -> float:
        """Wall-clock the warm-up will consume; entry is delayed by this."""
        return max(0, self.polls - 1) * self.interval_seconds

    def validate(self) -> None:
        if self.polls < 1:
            raise WarmupConfigurationError(f"polls must be >= 1, got {self.polls}")
        if self.interval_seconds <= 0:
            raise WarmupConfigurationError(
                f"interval_seconds must be > 0, got {self.interval_seconds}")
        widest = max(self.strides)
        landed = len(range(0, self.polls, widest))
        if landed < MIN_OBSERVATIONS_PER_STRIDE:
            raise WarmupConfigurationError(
                f"polls={self.polls} with widest stride {widest} leaves only {landed} "
                f"observations, below the {MIN_OBSERVATIONS_PER_STRIDE} the stability "
                f"gate needs. Raise polls to at least "
                f"{widest * MIN_OBSERVATIONS_PER_STRIDE}, or narrow the strides."
            )


def build_spot_only_snapshot(symbol: str, ltp: Optional[float], timestamp: str,
                             latency_ms: float) -> Optional[MarketSnapshot]:
    """One real spot reading as a MarketSnapshot, with every absent leg
    declared. Returns None when there is no price -- never a placeholder."""
    if ltp is None:
        return None
    return MarketSnapshot(
        snapshot_version=SNAPSHOT_VERSION,
        timestamp=timestamp,
        source=WARMUP_SOURCE,
        latency_ms=latency_ms,
        health_status=HEALTH_DEGRADED,
        missing_fields=_ABSENT,
        spot=SpotSnapshot(symbol=symbol, ltp=float(ltp)),
        vix=VixSnapshot(value=None),
        futures=None,
        option_chain=None,
    )


def poll_spot_series(
    fetch_spot: Callable[[], Optional[float]],
    clock: Callable[[], object],
    plan: WarmupPlan,
    symbol: str = "NSE:NIFTY50-INDEX",
    sleep: Callable[[float], None] = _time.sleep,
    logger=None,
) -> List[MarketSnapshot]:
    """Poll `fetch_spot` `plan.polls` times, `plan.interval_seconds` apart.

    Returns only the polls that produced a real price. A failed or empty
    poll is skipped and counted in the log rather than back-filled from the
    previous one -- repeating a stale price would manufacture a
    DUPLICATE_OBSERVATION and teach the builder that the market stood still.
    """
    plan.validate()
    snapshots: List[MarketSnapshot] = []
    missed = 0
    for index in range(plan.polls):
        if index:
            sleep(plan.interval_seconds)
        started = _time.monotonic()
        try:
            ltp = fetch_spot()
        except Exception as exc:  # noqa: BLE001 -- one bad poll must not end the warm-up.
            missed += 1
            if logger:
                logger.warning("warmup poll %d failed: %s: %s", index + 1,
                               type(exc).__name__, exc)
            continue
        latency_ms = (_time.monotonic() - started) * 1000.0
        snapshot = build_spot_only_snapshot(symbol, ltp, clock().isoformat(), latency_ms)
        if snapshot is None:
            missed += 1
            if logger:
                logger.warning("warmup poll %d returned no price -- skipped, not "
                               "back-filled.", index + 1)
            continue
        snapshots.append(snapshot)
    if logger:
        logger.info("WARMUP -- %d/%d polls yielded a real price (%d missed) over %.0fs",
                    len(snapshots), plan.polls, missed, plan.duration_seconds)
    return snapshots
