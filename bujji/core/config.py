"""Configuration management.

Every tunable parameter of the system lives here and is loaded from a YAML
file (with environment-variable overrides for secrets). Nothing is hardcoded
in the trading logic — modules receive a validated :class:`AppConfig` via
dependency injection.
"""
from __future__ import annotations

import os
from datetime import time
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, Field, field_validator


def _parse_time(value: str | time) -> time:
    if isinstance(value, time):
        return value
    hh, mm = value.strip().split(":")[:2]
    return time(hour=int(hh), minute=int(mm))


class MarketConfig(BaseModel):
    underlying: str = "NIFTY"
    exchange: str = "NSE"
    strike_interval: int = 50
    lot_size: int = 75
    # How the weekly expiry is chosen: "nearest_weekly" resolved by broker.
    expiry_selection: str = "nearest_weekly"
    # VWAP is computed from real per-candle volume (FYERS supplies this for the
    # index via the historical endpoint). Only enable the equal-weight
    # approximation for feeds that genuinely report zero volume (never FYERS);
    # when False and volume is absent, the engine will not trade.
    vwap_equal_weight_fallback: bool = False


class TimingConfig(BaseModel):
    candle_minutes: int = 5
    orb_start: time = Field(default=time(9, 15))
    orb_end: time = Field(default=time(9, 20))
    trading_start: time = Field(default=time(9, 20))
    trading_end: time = Field(default=time(15, 15))
    hard_exit: time = Field(default=time(15, 15))

    @field_validator("orb_start", "orb_end", "trading_start", "trading_end",
                     "hard_exit", mode="before")
    @classmethod
    def _coerce_time(cls, v):
        return _parse_time(v)


class RiskConfig(BaseModel):
    lots: int = 1
    max_mtm_loss: float = 6000.0      # Rupees; positive number, treated as loss cap.
    daily_loss_limit: float = 6000.0  # Rupees.
    breakout_body_ratio: float = 0.60  # Body must be >= 60% of range.
    # Opt-in take-profit for the tick-driven risk monitor (Tick Engine). None
    # (the default) disables it entirely — zero behavior change from before
    # this existed. This does not exist anywhere else in the strategy; it is
    # a new, explicitly-opt-in safety knob, not an inferred/invented default.
    max_mtm_profit: Optional[float] = None


class StrategyConfig(BaseModel):
    max_trades_per_day: int = 1
    allow_reentry: bool = False


class BrokerConfig(BaseModel):
    name: str = "paper"  # "paper" | "fyers".
    # Secrets are pulled from env, never committed to YAML.
    app_id: Optional[str] = None
    access_token: Optional[str] = None
    # Verified live (docs/FYERS_TOKEN_LIFECYCLE.md): FYERS supports automatic
    # daily access_token renewal via a refresh_token grant, valid ~15 days,
    # requiring the account's trading PIN (not the login password/TOTP).
    # All three optional — automatic renewal simply doesn't activate without
    # them (falls back to requiring a manual re-login, exactly as before).
    app_secret: Optional[str] = None
    refresh_token: Optional[str] = None
    pin: Optional[str] = None
    # Where a refreshed access_token/refresh_token gets written back to, so a
    # process restart picks up the renewed token instead of the stale one
    # baked into the environment at process start. None disables persistence
    # (renewal still works in-memory for the life of the process).
    credentials_file: Optional[str] = None
    retry_attempts: int = 3
    retry_backoff_seconds: float = 1.5
    poll_interval_seconds: float = 1.0
    order_timeout_seconds: float = 15.0


class PathsConfig(BaseModel):
    log_dir: Path = Path("logs")
    data_dir: Path = Path("data")
    journal_csv: Path = Path("data/trade_journal.csv")
    database: Path = Path("data/bujji.db")
    state_file: Path = Path("data/session_state.json")
    # Single-instance guard (F4): a second process pointed at the same lock
    # file refuses to start rather than risk duplicate/conflicting orders.
    lock_file: Path = Path("data/bujji.lock")


class DashboardConfig(BaseModel):
    enabled: bool = True
    host: str = "127.0.0.1"
    port: int = 8787
    refresh_seconds: int = 5


class AppConfig(BaseModel):
    """Top-level immutable-ish configuration object."""

    market: MarketConfig = MarketConfig()
    timing: TimingConfig = TimingConfig()
    risk: RiskConfig = RiskConfig()
    strategy: StrategyConfig = StrategyConfig()
    broker: BrokerConfig = BrokerConfig()
    paths: PathsConfig = PathsConfig()
    dashboard: DashboardConfig = DashboardConfig()
    log_level: str = "INFO"

    @classmethod
    def load(cls, path: str | Path) -> "AppConfig":
        """Load configuration from a YAML file with env-var secret overrides."""
        data: dict = {}
        p = Path(path)
        if p.exists():
            data = yaml.safe_load(p.read_text()) or {}
        cfg = cls(**data)
        # Secrets always come from the environment when present.
        cfg.broker.app_id = os.getenv("FYERS_APP_ID", cfg.broker.app_id)
        cfg.broker.access_token = os.getenv(
            "FYERS_ACCESS_TOKEN", cfg.broker.access_token
        )
        # Optional — enable automatic daily token renewal (see
        # docs/FYERS_TOKEN_LIFECYCLE.md). Absent by default; nothing changes
        # if these aren't set.
        cfg.broker.app_secret = os.getenv("FYERS_APP_SECRET", cfg.broker.app_secret)
        cfg.broker.refresh_token = os.getenv(
            "FYERS_REFRESH_TOKEN", cfg.broker.refresh_token
        )
        cfg.broker.pin = os.getenv("FYERS_PIN", cfg.broker.pin)
        cfg.broker.credentials_file = os.getenv(
            "FYERS_CREDENTIALS_FILE", cfg.broker.credentials_file
        )
        return cfg

    def ensure_dirs(self) -> None:
        for d in (self.paths.log_dir, self.paths.data_dir):
            Path(d).mkdir(parents=True, exist_ok=True)
