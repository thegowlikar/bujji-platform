"""Historical Replay Engine.

Drives a sequence of historical candles through the *identical* live decision
path — the same Signal Engine, Market Brain, Trade Manager, Execution Engine,
finite state machine, journal, and event bus that run in production. The only
substitutions are the :class:`ReplayBroker` (deterministic fills/pricing) and
an in-memory clock advanced by the candles themselves.

Because nothing about the decision logic is re-implemented here, a replay is a
faithful backtest: what it decides is exactly what the live bot would have
decided given the same candles.
"""
from __future__ import annotations

import csv
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

from ..core.config import AppConfig
from ..core.event_bus import Event, EventBus, EventType
from ..core.models import Candle
from ..core.orchestrator import Orchestrator
from ..core.runtime_status import RuntimeStatus
from ..core.session_state import SessionStore
from ..execution.engine import ExecutionEngine
from ..journal.journal import TradeJournal
from ..signal.engine import SignalEngine
from ..trade.manager import TradeManager
from .broker import ReplayBroker


@dataclass
class ReplayResult:
    candles_processed: int = 0
    final_state: str = ""
    trades: list[dict] = field(default_factory=list)
    events: list[str] = field(default_factory=list)


def load_candles_csv(path: str | Path) -> list[Candle]:
    """Load candles from a CSV with columns: timestamp,open,high,low,close,volume.

    ``timestamp`` may be ISO-8601 or an epoch-seconds integer.
    """
    rows: list[Candle] = []
    with Path(path).open(newline="") as fh:
        for r in csv.DictReader(fh):
            ts = r["timestamp"]
            when = (datetime.fromtimestamp(int(ts)) if ts.isdigit()
                    else datetime.fromisoformat(ts))
            rows.append(Candle(
                timestamp=when,
                open=float(r["open"]), high=float(r["high"]),
                low=float(r["low"]), close=float(r["close"]),
                volume=float(r.get("volume", 0) or 0),
            ))
    return rows


class ReplayEngine:
    """Constructs the full live stack over a ReplayBroker and feeds candles."""

    def __init__(self, config: AppConfig, logger: Optional[logging.Logger] = None):
        self._cfg = config
        self._log = logger or logging.getLogger("bujji.replay")
        if not self._log.handlers:
            self._log.addHandler(logging.NullHandler())
        self._broker = ReplayBroker()
        self._status = RuntimeStatus()
        self._bus = EventBus(self._log)
        self._journal = TradeJournal(config.paths.journal_csv, config.paths.database)
        self._store = SessionStore(config.paths.state_file)
        self._orch = Orchestrator(
            config, self._log,
            SignalEngine(config, self._log),
            TradeManager(config, self._log),
            ExecutionEngine(self._broker, config, self._log),
            self._journal, self._store, self._status, self._bus,
        )
        self._events: list[str] = []
        for et in EventType:
            self._bus.subscribe(et, self._capture_event)

    def _capture_event(self, event: Event) -> None:
        self._events.append(f"{event.type.value}: {event.payload}")

    async def run(self, candles: Iterable[Candle]) -> ReplayResult:
        await self._orch.startup()
        processed = 0
        for candle in candles:
            self._broker.set_market(candle.close)
            await self._orch.on_candle(candle)
            processed += 1
        return ReplayResult(
            candles_processed=processed,
            final_state=self._orch.state.value,
            trades=self._journal.all_trades(),
            events=self._events,
        )

    @property
    def status(self) -> RuntimeStatus:
        return self._status

    @property
    def orchestrator(self) -> Orchestrator:
        """Exposed so recovery / end-of-day paths can be driven in tests."""
        return self._orch
