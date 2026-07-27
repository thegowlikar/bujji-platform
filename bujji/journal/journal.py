"""Trade journal — persists every completed trade to CSV and SQLite.

The journal is write-through: each closed trade is appended to a CSV (for quick
human/Excel inspection) and inserted into a SQLite table (for the dashboard and
future analytics). Schema mirrors the fields required by the spec, plus the
actual traded contract identity (trade_id/ce_symbol/pe_symbol/expiry) so a
historical trade can always be traced back to the exact contracts sold, not
just the derived strike number.

Failure mode: CSV append and SQLite insert are two independent writes, not one
atomic transaction. `record()` never lets a failure in either half crash the
caller (see the docstring on `record` for why) — but it never fails silently
either: any mismatch between the two stores is logged at CRITICAL so an
operator investigating a discrepancy has a starting point in the logs instead
of nothing.
"""
from __future__ import annotations

import csv
import logging
import sqlite3
from dataclasses import asdict, dataclass, fields
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
    # Actual traded contract identity — added so a historical record always
    # preserves the exact symbols/expiry sold, not just the numeric strike.
    # Defaulted to "" for backward compatibility with any pre-existing CSV
    # rows written before these fields existed.
    trade_id: str = ""
    ce_symbol: str = ""
    pe_symbol: str = ""
    expiry: str = ""
    # Capital Management Engine's entry-time sizing decision -- permanent
    # audit trail of WHY this many lots were sold (or that the operator's
    # configured ceiling was the binding constraint, not available capital).
    capital_status: str = ""
    # Decision Lineage (Sprint 2): same decision_id as the TradeIntention/
    # DecisionSnapshot/ExecutionPlan that produced this trade -- the final
    # link letting Learning reconstruct "what was known, what was decided,
    # what happened" without inference. Defaulted for backward
    # compatibility with any pre-lineage row.
    decision_id: str = ""
    approved_lots: int = 0
    capital_utilization: str = ""


class TradeJournal:
    """CSV + SQLite persistence for closed trades."""

    def __init__(self, csv_path: Path, db_path: Path,
                 logger: Optional[logging.Logger] = None) -> None:
        self._csv = Path(csv_path)
        self._db = Path(db_path)
        self._csv.parent.mkdir(parents=True, exist_ok=True)
        self._db.parent.mkdir(parents=True, exist_ok=True)
        self._log = logger or logging.getLogger("bujji.journal")
        self._init_db()

    def _columns(self) -> list[str]:
        return [f.name for f in fields(TradeRecord)]

    def _init_db(self) -> None:
        cols = ", ".join(f"{c} TEXT" for c in self._columns())
        with sqlite3.connect(self._db) as conn:
            conn.execute(f"CREATE TABLE IF NOT EXISTS trades (id INTEGER PRIMARY KEY "
                         f"AUTOINCREMENT, {cols})")
            existing = {row[1] for row in conn.execute("PRAGMA table_info(trades)")}
            for col in self._columns():
                if col not in existing:
                    # Schema migration for a DB created before these columns
                    # existed — ALTER TABLE ADD COLUMN, never a destructive
                    # rebuild, so historical rows are never touched.
                    conn.execute(f"ALTER TABLE trades ADD COLUMN {col} TEXT")

    def record(self, trade: TradeRecord) -> None:
        """Write a completed trade to both stores.

        Deliberately never raises: this is called from the exit path AFTER
        the broker-side flatten has already succeeded, and MORE code runs
        after this call (publishing POSITION_CLOSED, clearing the trade
        manager's position, persisting session state) — letting a journal
        write failure propagate would abort that cleanup with the broker
        already flat but the in-memory state believing it's still exiting,
        which is a strictly worse outcome than a logged CSV/SQLite mismatch.
        """
        csv_ok = self._safe_append_csv(trade)
        db_ok = self._safe_insert_db(trade)
        if csv_ok and not db_ok:
            self._log.critical(
                "journal_write_partial: CSV row written but SQLite insert "
                "failed for trade entry_time=%s exit_time=%s — dashboard/"
                "analytics reads will be missing this trade until manually "
                "reconciled from the CSV.", trade.entry_time, trade.exit_time,
            )
        elif db_ok and not csv_ok:
            self._log.critical(
                "journal_write_partial: SQLite row written but CSV append "
                "failed for trade entry_time=%s exit_time=%s — the CSV "
                "export will be missing this trade.",
                trade.entry_time, trade.exit_time,
            )
        elif not csv_ok and not db_ok:
            self._log.critical(
                "journal_write_failed: BOTH CSV and SQLite writes failed for "
                "trade entry_time=%s exit_time=%s — this trade is not "
                "recorded anywhere except in-process logs; recover it "
                "manually from the structured JSON logs.",
                trade.entry_time, trade.exit_time,
            )

    def _safe_append_csv(self, trade: TradeRecord) -> bool:
        try:
            self._append_csv(trade)
            return True
        except OSError as exc:
            self._log.critical("journal_csv_write_failed: %s", exc)
            return False

    def _safe_insert_db(self, trade: TradeRecord) -> bool:
        try:
            self._insert_db(trade)
            return True
        except sqlite3.Error as exc:
            self._log.critical("journal_db_write_failed: %s", exc)
            return False

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
