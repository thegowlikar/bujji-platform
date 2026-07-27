"""Shadow Trading Engine engine — Series 100.

Deliverable 1 reuse, enforced structurally by import (not by pattern
only, unlike several prior series' "reused by pattern" disclosures):
`bujji.msi_position_lifecycle.engine.assess_position_lifecycle` is
called DIRECTLY -- this package contains no lifecycle logic of its
own. Real mark-to-market is computed by looking up each entry leg's
SAME (strike, expiry, option_type) contract in a LATER real Bhavcopy
day's chain -- genuinely real, not synthetic, since the same-expiry
contract is still listed until it actually expires.
"""
from __future__ import annotations

import hashlib
from typing import Optional, Sequence, Tuple

from bujji.msi_position_lifecycle.engine import assess_position_lifecycle
from bujji.msi_trade_construction.models import StrikeLeg, TradeConstructionAssessment
from bujji.msi_trade_thesis.models import TradeThesisAssessment

from . import config as _config
from . import taxonomy
from .models import Explanation, ShadowPosition


def _shadow_trade_id(decision_id: str, execution_plan_id: str, schema_version: str) -> str:
    content = "|".join([decision_id, execution_plan_id, schema_version])
    return hashlib.md5(content.encode("utf-8")).hexdigest()


def open_shadow_position(
    decision_id: str, execution_plan_id: str, trade: TradeConstructionAssessment,
    strategy_family: str, construction_type: str, simulated_margin: Optional[float],
    entry_date: str, position_close_date: str, *, timestamp: str,
) -> ShadowPosition:
    """Deliverable 3: records the theoretical fill -- real net entry
    price (`trade.expected_credit_debit`), real legs, real timestamp.
    Never submits an order, never estimates a fill price that isn't
    already the real, computed construction price."""
    schema_version = taxonomy.MSI_SHADOW_TRADING_VERSION
    aid = _shadow_trade_id(decision_id, execution_plan_id, schema_version)
    why_entered = (
        f"Decision Auditor recorded a TRADE_APPROVED decision (decision_id={decision_id}) with a real "
        f"execution plan (plan_id={execution_plan_id}) -- entry price {trade.expected_credit_debit} is "
        f"Series 90's own real constructed value, never re-estimated here",
    )
    explanation = Explanation(
        assessment_id=aid, why_entered=why_entered, why_current_state=("just opened",),
        why_exit_or_still_open=("position just opened -- not yet evaluated for exit",),
        schema_version=schema_version,
    )
    return ShadowPosition(
        shadow_trade_id=aid, decision_id=decision_id, execution_plan_id=execution_plan_id,
        entry_time=timestamp, entry_date=entry_date, entry_price=trade.expected_credit_debit or 0.0,
        entry_structure=f"{strategy_family} ({construction_type})", entry_legs=trade.legs,
        position_close_date=position_close_date, simulated_margin=simulated_margin,
        lifecycle_state="NEWLY_OPENED", realised_pnl=None, unrealised_pnl=0.0, exit_time=None,
        exit_reason=None, completed=False, explanation=explanation,
        provenance="bujji.msi_shadow_trading.engine.open_shadow_position", schema_version=schema_version,
    )


def _current_price(row) -> Optional[float]:
    """Real, discovered Bhavcopy data-quality finding: on a contract's OWN
    expiry day, its `settlement` field was observed to equal the
    UNDERLYING's settlement price (23913.7 for a NIFTY 24100 CE expiring
    that day, matching `underlying_price` exactly), not the option's own
    value -- while `close` correctly showed 0.15, the real, near-zero
    value of a deep-OTM expiring call. `close` is therefore preferred for
    repricing an ALREADY-HELD position on a later day; `settlement` is
    used only as a fallback when `close` is unavailable. Series 90's own
    entry-day construction still uses `settlement` deliberately (real,
    validated across this whole corpus for ENTRY pricing) -- this
    preference is specific to REPRICING an existing position later,
    disclosed here as a distinct, narrower fix."""
    if row.close is not None and row.close > 0:
        return row.close
    return row.settlement


def _current_net_value(legs: Sequence[StrikeLeg], current_chain: Sequence) -> Optional[float]:
    """Real reprice: sum each leg's CURRENT real price (same sign
    convention as Series 90's `expected_credit_debit` -- SELL
    contributes positively, BUY negatively, each x ratio). Returns None
    (never a guess) if any leg's exact contract cannot be found in
    today's real chain."""
    total = 0.0
    for leg in legs:
        match = next(
            (row for row in current_chain
             if row.strike == leg.strike and row.option_type == leg.option_type and row.expiry == leg.expiry),
            None,
        )
        price = _current_price(match) if match is not None else None
        if price is None:
            return None
        sign = 1.0 if leg.side == "SELL" else -1.0
        total += sign * price * leg.ratio
    return round(total, 4)


def track_shadow_position(
    position: ShadowPosition, entry_thesis: TradeThesisAssessment, current_thesis: TradeThesisAssessment,
    portfolio, current_chain: Sequence, current_date: str, *, timestamp: str,
) -> ShadowPosition:
    """Deliverable 4/5: one real day's tracking update. REUSES Position
    Lifecycle's own `assess_position_lifecycle` directly -- no
    lifecycle logic is re-implemented here. Exits using EXACTLY the
    lifecycle states production will use -- never a shadow-only rule."""
    if position.completed:
        return position
    schema_version = taxonomy.MSI_SHADOW_TRADING_VERSION

    strategy_family, construction_type = position.entry_structure.rsplit(" (", 1)
    construction_type = construction_type.rstrip(")")

    pli = assess_position_lifecycle(
        entry_thesis, current_thesis, strategy_family, construction_type, portfolio,
        position.entry_date, current_date, position.position_close_date, timestamp=timestamp,
    )

    current_net_value = _current_net_value(position.entry_legs, current_chain)
    unrealised_pnl = None
    if current_net_value is not None:
        lot_size = _config.DEFAULT_LOT_SIZE
        unrealised_pnl = round((position.entry_price - current_net_value) * lot_size, 2)

    should_exit = pli.position_state in taxonomy.EXIT_TRIGGERING_LIFECYCLE_STATES
    exit_reason = None
    exit_time = None
    completed = False
    realised_pnl = None
    if should_exit:
        exit_reason = (
            taxonomy.EXIT_REASON_HARD_SESSION_CLOSE if pli.position_state == "CLOSED"
            else pli.position_state  # THESIS_BROKEN / EXIT_CANDIDATE / PROFIT_HARVEST, verbatim.
        )
        exit_time = timestamp
        completed = True
        realised_pnl = unrealised_pnl  # The mark AT exit becomes the realised figure -- never re-estimated.

    why_current_state = (
        f"Position Lifecycle (Series 96) reports position_state={pli.position_state} on {current_date} "
        f"(entry thesis={entry_thesis.thesis_type}, current thesis={current_thesis.thesis_type})",
    )
    why_exit = (
        (f"exiting: lifecycle state {pli.position_state} is an exit-triggering state -- reused directly, "
         f"never a shadow-only rule",)
        if should_exit else
        ("still open -- lifecycle state does not warrant exit today",)
    )

    aid = _shadow_trade_id(position.decision_id, position.execution_plan_id + f":{current_date}", schema_version)
    explanation = Explanation(
        assessment_id=aid, why_entered=position.explanation.why_entered,
        why_current_state=why_current_state, why_exit_or_still_open=why_exit, schema_version=schema_version,
    )

    return ShadowPosition(
        shadow_trade_id=position.shadow_trade_id, decision_id=position.decision_id,
        execution_plan_id=position.execution_plan_id, entry_time=position.entry_time,
        entry_date=position.entry_date, entry_price=position.entry_price,
        entry_structure=position.entry_structure, entry_legs=position.entry_legs,
        position_close_date=position.position_close_date, simulated_margin=position.simulated_margin,
        lifecycle_state=pli.position_state, realised_pnl=realised_pnl, unrealised_pnl=unrealised_pnl,
        exit_time=exit_time, exit_reason=exit_reason, completed=completed, explanation=explanation,
        provenance="bujji.msi_shadow_trading.engine.track_shadow_position", schema_version=schema_version,
    )
