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
    # Renamed in role (not in field name, to avoid a config.yaml migration):
    # this is now the OPERATOR CEILING -- the Capital Management Engine
    # (bujji/capital/) may approve FEWER lots than this if available margin
    # does not support it, but will never approve MORE. No strategy module
    # multiplies this by lot_size directly anymore.
    lots: int = 1  # configured_max_lots
    margin_safety_buffer: float = 0.90  # Only this fraction of available
                                          # margin may be allocated; the rest
                                          # stays unused headroom.
    # Capital policy (see bujji/capital/policy.py for the full contract):
    #   STRICT     -- only a broker-CERTIFIED margin figure may be used;
    #                  anything uncertified -> no trade. The only policy
    #                  safe for real capital without also setting
    #                  margin_provider_certified below.
    #   ESTIMATED  -- uses estimated_margin_per_lot below (an operator
    #                  guess, never broker-verified).
    #   SIMULATION -- uses simulated_margin_per_lot below (pure synthetic,
    #                  for replay/dev; not tied to any real account).
    #   CERTIFIED  -- uses the broker's margin calculator AND requires
    #                  margin_provider_certified: true as a separate,
    #                  explicit confirmation that a human verified it live.
    capital_policy: str = "STRICT"
    estimated_margin_per_lot: Optional[float] = None
    simulated_margin_per_lot: float = 1_00_000.0
    margin_provider_certified: bool = False
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
    # Decision Journal foundation (Sprint 2) -- separate from journal_csv/
    # database, deliberately: DecisionSnapshots are keyed by decision_id
    # and joined against the trade journal by a future Learning Layer,
    # never merged into TradeJournal's own schema.
    decision_journal: Path = Path("data/decision_journal.jsonl")
    # Operations Layer (Sprint 4) -- observational only, never read by any
    # trading decision.
    ops_restart_count: Path = Path("data/ops_restart_count.json")
    incident_log: Path = Path("data/incident_log.jsonl")
    # Single-instance guard (F4): a second process pointed at the same lock
    # file refuses to start rather than risk duplicate/conflicting orders.
    lock_file: Path = Path("data/bujji.lock")
    # Intelligence Observation Journal (Integration Series 2, Sprint 2) --
    # entirely separate from decision_journal above; never modifies it.
    intelligence_observation_journal: Path = Path("data/intelligence_observation_journal.jsonl")
    # Intelligence Evaluation Journal (Integration Series 3, Sprint 1) --
    # entirely separate from decision_journal and
    # intelligence_observation_journal above; never modifies either.
    intelligence_evaluation_journal: Path = Path("data/intelligence_evaluation_journal.jsonl")


class IntelligenceAdapterSettings(BaseModel):
    """Integration Series 1, Sprint 1 -- MIC v2 Intelligence Adapter.

    Strictly observational: reads MIC v2's published Consumer API
    (a durable JSONL journal) and, when enabled, records a snapshot
    REFERENCE alongside the existing Decision Journal entry. Never
    influences entry, exit, qualification, sizing, filtering, risk,
    execution, or broker communication -- disabled by default, and even
    when enabled, its only observable effect is three extra id fields
    on a DecisionJournal row (see bujji/core/orchestrator.py's own
    `_enter()` and docs/INTELLIGENCE_ADAPTER_ARCHITECTURE.md).
    """
    enabled: bool = False
    mic_v2_root: Path = Path("/opt/bujji-mic-v2")
    consumer_journal_path: Path = Path("/opt/bujji-mic-v2/qualification_campaign_1/consumer_journal.jsonl")
    # Continuous Intelligence Observation Framework (Integration Series 2,
    # Sprint 2) -- purely observational thresholds for the adapter's own
    # operational health; never read by any decision-making code (see
    # docs/INTELLIGENCE_OBSERVATION_ARCHITECTURE.md).
    observation_stale_after_seconds: float = 7 * 24 * 3600.0
    observation_degraded_latency_seconds: float = 0.5
    # Real Opinion Source Wiring (Integration Series 4, Sprint 1) --
    # location of MIC v2's Opinion Journal (Engineering Series 19,
    # Sprint 1 / Addendum 8). Read-only, never read by any
    # decision-making code (see docs/OPINION_SOURCE_WIRING_ARCHITECTURE.md).
    opinion_journal_path: Path = Path("/opt/bujji-mic-v2/qualification_campaign_1/opinion_journal.jsonl")


class DashboardConfig(BaseModel):
    enabled: bool = True
    host: str = "127.0.0.1"
    port: int = 8787
    refresh_seconds: int = 5
    # Dashboard Priority 1: show a STALE warning when the status hasn't updated
    # for this many seconds. Default 420s (7 min) is deliberately > one 5-min
    # candle cadence (the status advances at most once per completed candle
    # while idle, so a shorter threshold would false-alarm every cycle); this
    # flags a genuinely missed candle / hung loop, not normal idle waiting.
    stale_after_seconds: int = 420


class AppConfig(BaseModel):
    """Top-level immutable-ish configuration object."""

    market: MarketConfig = MarketConfig()
    timing: TimingConfig = TimingConfig()
    risk: RiskConfig = RiskConfig()
    strategy: StrategyConfig = StrategyConfig()
    broker: BrokerConfig = BrokerConfig()
    paths: PathsConfig = PathsConfig()
    dashboard: DashboardConfig = DashboardConfig()
    intelligence_adapter: IntelligenceAdapterSettings = IntelligenceAdapterSettings()
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
