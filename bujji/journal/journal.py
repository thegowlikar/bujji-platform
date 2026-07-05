"""Trade journal — persists every completed trade to CSV and SQLite.

The journal is write-through: each closed trade is appended to a CSV (for quick
human/Excel inspection) and inserted into a SQLite table (for the dashboard and
future analytics). Schema mirrors the fields required by the spec.
"""
from __future__ import annotations

import csv
import sqlite3
from dataclasses import asdict, dataclass, fields
from datetime import datetime
from pathlib import Path
from typing import Optional


@dataclass
class TradeRecord:
    date: str
    direction: str
    orb_high: float
    orb_low: float
    entry_time: str
    entry_spot: float
    atm_strike: int
    entry_premium: float
    exit_time: str
    exit_premium: float
    exit_spot: float
    exit_reason: str
    holding_time_min: float
    max_profit_seen: float
    max_loss_seen: float
    max_favourable_excursion: float
    max_adverse_excursion: float
    total_candles_held: int
    daily_result: float
    thesis: str = ""


class TradeJournal:
    """CSV + SQLite persistence for closed trades."""

    def __init__(self, csv_path: Path, db_path: Path) -> None:
        self._csv = Path(csv_path)
        self._db = Path(db_path)
        self._csv.parent.mkdir(parents=True, exist_ok=True)
        self._db.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _columns(self) -> list[str]:
        return [f.name for f in fields(TradeRecord)]

    def _init_db(self) -> None:
        cols = ", ".join(f"{c} TEXT" for c in self._columns())
        with sqlite3.connect(self._db) as conn:
            conn.execute(f"CREATE TABLE IF NOT EXISTS trades (id INTEGER PRIMARY KEY "
                         f"AUTOINCREMENT, {cols})")

    def record(self, trade: TradeRecord) -> None:
        self._append_csv(trade)
        self._insert_db(trade)

    def _append_csv(self, trade: TradeRecord) -> None:
        exists = self._csv.exists()
        with self._csv.open("a", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=self._columns())
            if not exists:
                writer.writeheader()
            writer.writerow(asdict(trade))

    def _insert_db(self, trade: TradeRecord) -> None:
        cols = self._columns()
        placeholders = ", ".join("?" for _ in cols)
        values = [str(getattr(trade, c)) for c in cols]
        with sqlite3.connect(self._db) as conn:
            conn.execute(
                f"INSERT INTO trades ({', '.join(cols)}) VALUES ({placeholders})",
                values,
            )

    def all_trades(self) -> list[dict]:
        with sqlite3.connect(self._db) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT * FROM trades ORDER BY id DESC").fetchall()
            return [dict(r) for r in rows]
