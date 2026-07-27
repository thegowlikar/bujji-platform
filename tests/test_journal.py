"""Trade journal — contract identity preservation and failure isolation.

Two things certified here that had no coverage before:
  1. The permanent record (CSV + SQLite) preserves the ACTUAL traded contract
     identity (trade_id, ce_symbol, pe_symbol, expiry) — not just the derived
     atm_strike int, which loses which specific weekly contract was sold.
  2. A failure in either store (CSV or SQLite) is logged loudly, never
     silent, and never crashes the caller — record() must remain safe to
     call from the exit path even if a write fails.
"""
import logging
import sqlite3

import pytest

from bujji.journal.journal import TradeJournal, TradeRecord


def _record(**overrides) -> TradeRecord:
    base = dict(
        date="2026-07-19", direction="NEUTRAL", orb_high=0.0, orb_low=0.0,
        entry_time="2026-07-19T09:20:00", entry_spot=22005.0, atm_strike=22000,
        entry_premium=240.0, exit_time="2026-07-19T15:05:00", exit_premium=230.0,
        exit_spot=22010.0, exit_reason="hard_exit_time", holding_time_min=345.0,
        max_profit_seen=15.0, max_loss_seen=-5.0, max_favourable_excursion=15.0,
        max_adverse_excursion=-5.0, total_candles_held=69, daily_result=750.0,
        thesis="", trade_id="20260719092000-NIFTY22000CE",
        ce_symbol="NIFTY22000CE", pe_symbol="NIFTY22000PE", expiry="2026-07-23",
    )
    base.update(overrides)
    return TradeRecord(**base)


def test_journal_preserves_actual_traded_contract_identity(tmp_path):
    journal = TradeJournal(tmp_path / "j.csv", tmp_path / "b.db")
    journal.record(_record())

    trades = journal.all_trades()
    assert len(trades) == 1
    assert trades[0]["ce_symbol"] == "NIFTY22000CE"
    assert trades[0]["pe_symbol"] == "NIFTY22000PE"
    assert trades[0]["expiry"] == "2026-07-23"
    assert trades[0]["trade_id"] == "20260719092000-NIFTY22000CE"

    # And the CSV copy carries the same fields (human/Excel inspection path).
    csv_text = (tmp_path / "j.csv").read_text()
    assert "ce_symbol" in csv_text and "NIFTY22000CE" in csv_text
    assert "pe_symbol" in csv_text and "NIFTY22000PE" in csv_text


def test_db_write_failure_is_logged_not_silent_and_does_not_raise(tmp_path, monkeypatch, caplog):
    journal = TradeJournal(tmp_path / "j.csv", tmp_path / "b.db")

    def boom(self, trade):
        raise sqlite3.OperationalError("simulated disk full")
    monkeypatch.setattr(TradeJournal, "_insert_db", boom)

    with caplog.at_level(logging.CRITICAL, logger="bujji.journal"):
        journal.record(_record())  # Must not raise.

    assert any("journal_write_partial" in m or "journal_db_write_failed" in m
               for m in caplog.messages)
    # The CSV half still succeeded — not silently lost.
    assert (tmp_path / "j.csv").exists()


def test_csv_write_failure_is_logged_not_silent_and_does_not_raise(tmp_path, monkeypatch, caplog):
    journal = TradeJournal(tmp_path / "j.csv", tmp_path / "b.db")

    def boom(self, trade):
        raise OSError("simulated permission denied")
    monkeypatch.setattr(TradeJournal, "_append_csv", boom)

    with caplog.at_level(logging.CRITICAL, logger="bujji.journal"):
        journal.record(_record())  # Must not raise.

    assert any("journal_write_partial" in m or "journal_csv_write_failed" in m
               for m in caplog.messages)
    # The SQLite half still succeeded — not silently lost.
    assert len(journal.all_trades()) == 1


def test_schema_migration_adds_missing_columns_to_existing_db(tmp_path):
    """A database created before ce_symbol/pe_symbol/expiry/trade_id existed
    must gain those columns via ALTER TABLE, never a destructive rebuild —
    existing historical rows must survive untouched."""
    db_path = tmp_path / "b.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE trades (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "date TEXT, direction TEXT, daily_result TEXT)"
        )
        conn.execute(
            "INSERT INTO trades (date, direction, daily_result) VALUES (?, ?, ?)",
            ("2026-07-01", "NEUTRAL", "500.0"),
        )

    journal = TradeJournal(tmp_path / "j.csv", db_path)
    journal.record(_record())

    trades = journal.all_trades()
    assert len(trades) == 2  # Old row survived, new row added.
    old_row = next(t for t in trades if t["date"] == "2026-07-01")
    assert old_row["daily_result"] == "500.0"  # Untouched.
    new_row = next(t for t in trades if t["date"] == "2026-07-19")
    assert new_row["ce_symbol"] == "NIFTY22000CE"
