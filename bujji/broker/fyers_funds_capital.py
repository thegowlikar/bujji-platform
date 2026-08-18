"""Real capital snapshots from the live-certified FYERS funds endpoint.

WHAT THIS REPLACES: the runner's capital_snapshot_provider closure conjured a
CapitalSafetySnapshot from `capital_snapshot:` config with defaults totalling
one crore of capital that does not exist (`capital_snapshot: {}` in the
production YAML meant EVERY field was a default). Real broker-quoted margin
(2026-08-18) was being compared against that fiction. This module supplies the
capital side from `FyersBroker.get_funds()` -- live-certified 2026-07-19 with
the real fund_limit row titles (docs/CAPITAL_MANAGEMENT_ENGINE.md).

WHAT IS REAL, WHAT IS POLICY, WHAT IS ZERO-STATE -- three kinds of field,
never mixed silently:

  broker-real   total_capital (Total Balance), available_capital (Available
                Balance), used_margin (Utilized Amount). Refreshed through the
                shared pacer; a refresh failure keeps the last real snapshot
                until MAX_STALENESS, then this provider RAISES -- the risk
                context's own error handling turns that into
                ContextUnavailable, which blocks entries honestly.
  session-real  peak_capital: the maximum account equity THIS provider has
                observed this session. Starts at the first real reading.
  policy        daily_loss_limit, max_allowed_drawdown: operator-owned risk
                limits, REQUIRED in config -- there are no defaults here, a
                missing limit is a construction error, never a guess.
  zero-state    open_risk, reserved_risk, daily_pnl, consecutive_losses: 0 at
                session start, which is TRUE (no open positions, nothing
                realized). DISCLOSED LIMITATION: they stay 0 through the
                session -- the closure this replaces returned static config
                constants for the same fields, so this is no regression, but
                a position opened mid-session is not yet reflected here.

The runner is synchronous; `get_funds()` is async. Following the runner's own
established pattern, the default fetch wraps the call in `asyncio.run()` --
and the broker is built lazily on first use so constructing this provider
performs no I/O at all.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, Optional

from bujji.trading_brain.risk_governor.capital_safety_governor import CapitalSafetySnapshot

LOG = logging.getLogger("bujji.broker.fyers_funds_capital")

REQUIRED_POLICY_KEYS = ("daily_loss_limit", "max_allowed_drawdown")
# The three get_funds fields a snapshot cannot honestly be built without.
REQUIRED_FUNDS_FIELDS = ("account_equity", "available_funds", "used_margin")

DEFAULT_REFRESH_INTERVAL_SECONDS = 60.0
DEFAULT_MAX_STALENESS_SECONDS = 900.0


class CapitalRealityUnavailable(RuntimeError):
    """No sufficiently fresh real funds snapshot exists. Raised instead of
    guessing; the live risk context converts provider exceptions into
    ContextUnavailable, so entries are blocked, never mis-sized."""


def _default_fetch() -> Optional[Dict[str, Any]]:
    """Lazy broker construction + one funds call, runner-style asyncio.run."""
    import asyncio

    from bujji.broker.fyers import FyersBroker
    from bujji.core.config import AppConfig

    cfg = AppConfig.load("config/config.yaml").broker
    if cfg.name != "fyers":
        cfg.name = "fyers"

    async def _go():
        broker = FyersBroker(cfg, LOG)
        await broker.connect()
        return await broker.get_funds()

    return asyncio.run(_go())


class FyersCapitalSnapshotProvider:
    """Callable matching the capital_snapshot_provider contract: () ->
    CapitalSafetySnapshot. Real capital, cached between refreshes, fail-closed
    on prolonged unavailability."""

    def __init__(self, *, policy: Dict[str, Any], clock: Callable[[], Any],
                 fetch: Optional[Callable[[], Optional[Dict[str, Any]]]] = None,
                 refresh_interval_seconds: float = DEFAULT_REFRESH_INTERVAL_SECONDS,
                 max_staleness_seconds: float = DEFAULT_MAX_STALENESS_SECONDS,
                 monotonic: Callable[[], float] = time.monotonic) -> None:
        missing = [k for k in REQUIRED_POLICY_KEYS if policy.get(k) is None]
        if missing:
            raise ValueError(
                f"capital_snapshot config must declare {missing} explicitly -- "
                "risk limits are operator decisions, never defaults.")
        self._policy = dict(policy)
        self._clock = clock
        self._fetch = fetch or _default_fetch
        self._refresh_interval = refresh_interval_seconds
        self._max_staleness = max_staleness_seconds
        self._monotonic = monotonic
        self._funds: Optional[Dict[str, Any]] = None
        self._fetched_at: Optional[float] = None
        self._peak_equity: Optional[float] = None
        self._last_error: Optional[str] = None

    def _age(self) -> Optional[float]:
        if self._fetched_at is None:
            return None
        return self._monotonic() - self._fetched_at

    def _try_refresh(self) -> None:
        try:
            funds = self._fetch()
            if funds is None:
                raise CapitalRealityUnavailable("get_funds returned None (broker said not-ok)")
            absent = [k for k in REQUIRED_FUNDS_FIELDS if funds.get(k) is None]
            if absent:
                raise CapitalRealityUnavailable(
                    f"funds response missing required fields {absent} -- refusing "
                    "to fabricate them")
        except Exception as exc:  # noqa: BLE001 -- recorded, then stale-cache rules decide
            self._last_error = f"{type(exc).__name__}: {exc}"
            LOG.warning("capital refresh failed (keeping last real snapshot): %s",
                        self._last_error)
            return
        self._funds = funds
        self._fetched_at = self._monotonic()
        self._last_error = None
        equity = float(funds["account_equity"])
        self._peak_equity = equity if self._peak_equity is None else max(self._peak_equity, equity)

    def __call__(self) -> CapitalSafetySnapshot:
        age = self._age()
        if age is None or age >= self._refresh_interval:
            self._try_refresh()
            age = self._age()
        if self._funds is None:
            raise CapitalRealityUnavailable(
                f"no real funds snapshot has ever succeeded (last error: {self._last_error})")
        if age is not None and age > self._max_staleness:
            raise CapitalRealityUnavailable(
                f"last real funds snapshot is {age:.0f}s old (max "
                f"{self._max_staleness:.0f}s; last error: {self._last_error}) -- "
                "refusing to represent stale capital as current")
        return CapitalSafetySnapshot(
            total_capital=float(self._funds["account_equity"]),
            available_capital=float(self._funds["available_funds"]),
            used_margin=float(self._funds["used_margin"]),
            open_risk=0.0,          # zero-state: true at session start; see module docstring
            reserved_risk=0.0,      # zero-state
            daily_pnl=0.0,          # zero-state
            daily_loss_limit=float(self._policy["daily_loss_limit"]),
            peak_capital=float(self._peak_equity),
            max_allowed_drawdown=float(self._policy["max_allowed_drawdown"]),
            consecutive_losses=0,   # zero-state
            timestamp=self._clock(),
        )
