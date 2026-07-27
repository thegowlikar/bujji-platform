"""Journal Recording — Production Pipeline Stage 7 (trade/exit half).

Production Engineering Sprint 4 (Stage Interface Extraction). This is an
EXTRACTION, not a rewrite: every field and computation here (holding-time
calculation, pos.mtm()-derived PnL, trade_id derivation, capital-decision
field parsing) is identical to what previously ran inline inside
Orchestrator._journal_trade_impl(). No rounding rule, no field mapping,
no default value has changed.

Pure and journal-free: takes only a Position and the exit inputs, returns
a TradeRecord. It never calls TradeJournal.record() and never logs --
those side effects stay in the orchestrator, which persists and logs the
record this function returns. This is what makes TradeRecord construction
independently unit-testable without a live TradeJournal.
"""
from __future__ import annotations

from datetime import datetime

from ..journal.journal import TradeRecord
from .models import Position


def build_trade_record(
    pos: Position,
    exit_time: datetime,
    exit_spot: float,
    exit_premium: float,
    reason: str,
) -> TradeRecord:
    holding_min = (exit_time - pos.entry_time).total_seconds() / 60.0
    return TradeRecord(
        date=pos.entry_time.date().isoformat(),
        direction=pos.direction.value,
        orb_high=pos.orb.high if pos.orb else 0.0,
        orb_low=pos.orb.low if pos.orb else 0.0,
        entry_time=pos.entry_time.isoformat(),
        entry_spot=pos.entry_spot,
        atm_strike=pos.contract.strike,
        entry_premium=pos.entry_price,
        exit_time=exit_time.isoformat(),
        exit_premium=exit_premium,
        exit_spot=exit_spot,
        exit_reason=reason,
        holding_time_min=round(holding_min, 1),
        max_profit_seen=round(pos.max_profit_seen, 2),
        max_loss_seen=round(pos.max_loss_seen, 2),
        max_favourable_excursion=round(pos.max_favourable_excursion, 2),
        max_adverse_excursion=round(pos.max_adverse_excursion, 2),
        total_candles_held=pos.candles_held,
        daily_result=round(pos.mtm(exit_premium), 2),
        thesis=pos.thesis.narrative if pos.thesis else "",
        # Actual traded contract identity — preserved permanently so a
        # historical trade can be traced back to the exact contracts
        # sold, not just the derived strike number (the live/transient
        # SessionSnapshot has this too, but it is reset every trading
        # day; the journal is the only PERMANENT record).
        trade_id=f"{pos.entry_time.strftime('%Y%m%d%H%M%S')}-{pos.contract.symbol}",
        decision_id=pos.decision_id,
        ce_symbol=pos.ce_contract.symbol if pos.ce_contract else pos.contract.symbol,
        pe_symbol=pos.pe_contract.symbol if pos.pe_contract else "",
        expiry=pos.contract.expiry,
        capital_status=(pos.capital_decision or {}).get("status", ""),
        approved_lots=(pos.capital_decision or {}).get("approved_lots", 0),
        capital_utilization=str((pos.capital_decision or {}).get("capital_utilization", "")),
    )
