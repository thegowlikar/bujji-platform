"""Shadow Trade Construction Bridge — Phase 14, Tasks 1-4 (scoped first
slice). Pure functions, no state, no broker call, no execution.

REUSE, NOT DUPLICATION: `bujji.msi_trade_construction.engine.construct_trade`
already implements real, tested, per-family leg-shape logic (expiry
selection, delta-targeted/ATM-anchored strike selection, wing-width sizing,
liquidity gating, risk-profile classification) for all 13 SSF families. It
was built against Bhavcopy-shaped rows (`.expiry .strike .option_type
.settlement .open_interest`), never against the live `MarketSnapshot.
option_chain`. Rather than reimplement that logic against live data (real
risk of silent divergence between two "same" strike-selection rules) or
blindly force live data into the Bhavcopy shape without understanding the
difference (explicitly forbidden by the Phase 14 brief), this bridge:

1. Builds a `_LiveChainRow` adapter satisfying EXACTLY the attribute
   contract `construct_trade`/`_build_strike_evidence` read (`.expiry`,
   `.strike`, `.option_type`, `.settlement`, `.open_interest`) — `.settlement`
   is the live bid/ask MID, a real quoted value, never fabricated, just
   carrying a different upstream name (the same substitution
   `HybridPaperBroker` already makes when it fills paper orders "against
   the real observed premium" instead of a synthetic one).
2. ONLY includes legs with a REAL, valid bid AND ask (both > 0) in the
   adapted chain — a leg with missing/invalid quote data is invisible to
   `construct_trade`, so it can never be silently substituted or guessed at.
3. Calls `construct_trade()` completely UNMODIFIED — same IV solve (real
   Black-Scholes against the live mid, same function `msi_volatility_structure`
   itself reuses), same delta targeting, same liquidity/OI gate, same
   rejection taxonomy.
4. Wraps the result into `ShadowTradeCandidate`, re-attaching the real
   bid/ask this package additionally preserves (which `TradeConstructionAssessment`
   does not carry) by matching legs back to the original live OptionLeg objects.

CALENDAR naturally, honestly fails closed here: the live option chain
(`market_perception.option_chain_adapter`) only ever fetches the SINGLE
nearest expiry (confirmed in Phase 9-11 investigation), so
`select_calendar_expiries` always finds zero later candidate expiries and
`construct_trade` returns `constructed=False,
rejection_reason=REJECT_NO_SUITABLE_EXPIRY` — no special-casing needed.

No hindsight: every input here (`snapshot`, `record`) is exactly what was
real and available AT the decision timestamp -- nothing here reads a later
cycle or a later snapshot.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from bujji.msi_trade_construction import taxonomy as tc_taxonomy
from bujji.msi_trade_construction.engine import construct_trade

from .models import ShadowTradeCandidate, ShadowTradeLeg, STATUS_CONSTRUCTED, STATUS_INSUFFICIENT_MARKET_DATA, \
    STATUS_INVALID_INTENT, STATUS_NOT_CONSTRUCTIBLE, STATUS_PARTIALLY_CONSTRUCTIBLE

_REJECTION_TO_STATUS = {
    tc_taxonomy.REJECT_NO_SUITABLE_EXPIRY: STATUS_INSUFFICIENT_MARKET_DATA,
    tc_taxonomy.REJECT_STRIKE_UNAVAILABLE: STATUS_INSUFFICIENT_MARKET_DATA,
    tc_taxonomy.REJECT_LIQUIDITY_INSUFFICIENT: STATUS_PARTIALLY_CONSTRUCTIBLE,
    tc_taxonomy.REJECT_IMPOSSIBLE_WING_WIDTH: STATUS_INSUFFICIENT_MARKET_DATA,
    tc_taxonomy.REJECT_INCONSISTENT_CHAIN: STATUS_INSUFFICIENT_MARKET_DATA,
    tc_taxonomy.REJECT_IV_UNSOLVABLE: STATUS_INSUFFICIENT_MARKET_DATA,
    tc_taxonomy.REJECT_UNSUPPORTED_FAMILY: STATUS_NOT_CONSTRUCTIBLE,
}


class _LiveChainRow:
    __slots__ = ("expiry", "strike", "option_type", "settlement", "open_interest")

    def __init__(self, expiry, strike, option_type, settlement, open_interest):
        self.expiry = expiry
        self.strike = strike
        self.option_type = option_type
        self.settlement = settlement
        self.open_interest = open_interest


def _safe_get(d, *path, default=None):
    cur = d
    for k in path:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k)
    return default if cur is None else cur


def _build_adapted_chain(option_chain) -> List[_LiveChainRow]:
    """Only legs with a REAL bid AND ask (both > 0) are represented --
    everything else is simply absent from the chain `construct_trade`
    sees, so it fails closed on that strike rather than guessing."""
    rows = []
    if option_chain is None:
        return rows
    for leg in option_chain.legs:
        if leg.bid is None or leg.ask is None or leg.bid <= 0 or leg.ask <= 0:
            continue
        mid = round((leg.bid + leg.ask) / 2.0, 4)
        rows.append(_LiveChainRow(
            expiry=option_chain.expiry, strike=leg.strike, option_type=leg.option_type,
            settlement=mid, open_interest=leg.open_interest,
        ))
    return rows


def _candidate_id(source_cycle_id: str, family: str) -> str:
    return "STC-" + hashlib.md5(f"{source_cycle_id}|{family}".encode()).hexdigest()[:24]


def build_shadow_trade_candidate(
    record: dict, snapshot, min_dte: int = 0, max_dte: int = 10,
) -> ShadowTradeCandidate:
    """Pure function: one already-persisted intelligence_cycle `record`
    (dict) + its associated `MarketSnapshot` (the SAME cycle's snapshot,
    never a later one) -> one ShadowTradeCandidate. Never raises; every
    failure path returns a real, explained, non-CONSTRUCTED candidate."""
    source_cycle_id = record.get("timestamp", "")
    source_snapshot_id = getattr(snapshot, "timestamp", "") if snapshot is not None else ""
    trade_intent = record.get("trade_intent")
    strategy_selection = record.get("strategy_selection") or {}
    family = strategy_selection.get("selected_strategy_family")

    market_regime = _safe_get(record, "market_state", "regime")
    direction = _safe_get(record, "market_direction", "overall_direction")
    thesis = _safe_get(record, "trade_thesis", "thesis_type")
    selection_confidence = strategy_selection.get("confidence")
    consensus_state = _safe_get(record, "consensus", "consensus_level")
    opportunity_state = _safe_get(record, "opportunity", "opportunity_state")
    expected_move_pct = _safe_get(record, "volatility_structure", "expected_move_pct")

    underlying_symbol = getattr(getattr(snapshot, "spot", None), "symbol", None) or "NIFTY"
    underlying_price = getattr(getattr(snapshot, "spot", None), "ltp", None) if snapshot is not None else None

    common_kwargs = dict(
        candidate_id=_candidate_id(source_cycle_id, family or "NONE"),
        timestamp=source_cycle_id,
        source_cycle_id=source_cycle_id,
        strategy_family=family,
        strategy_variant=None,
        market_regime=market_regime,
        direction=direction,
        thesis=thesis,
        selection_confidence=selection_confidence,
        consensus_state=consensus_state,
        opportunity_state=opportunity_state,
        underlying_symbol=underlying_symbol,
        underlying_price=underlying_price,
        source_market_snapshot_id=source_snapshot_id,
        evidence_snapshot={
            "market_direction": direction, "consensus": consensus_state,
            "opportunity": opportunity_state, "trade_thesis": thesis,
        },
    )

    if family is None or trade_intent is None:
        return ShadowTradeCandidate(
            **common_kwargs, expiry=None, legs=(), quantity=None, lot_size=None, contracts=None,
            construction_confidence="NONE", construction_status=STATUS_INVALID_INTENT,
            construction_reason="no strategy was selected / no TradeIntent produced this cycle -- honestly nothing to construct",
            required_evidence=("strategy_selection", "trade_intent"),
        )

    if underlying_price is None or underlying_price <= 0:
        return ShadowTradeCandidate(
            **common_kwargs, expiry=None, legs=(), quantity=None, lot_size=None, contracts=None,
            construction_confidence="NONE", construction_status=STATUS_INSUFFICIENT_MARKET_DATA,
            construction_reason="no real underlying spot price available this cycle",
            required_evidence=("underlying_price",),
        )

    option_chain = getattr(snapshot, "option_chain", None) if snapshot is not None else None
    adapted_chain = _build_adapted_chain(option_chain)
    if not adapted_chain:
        return ShadowTradeCandidate(
            **common_kwargs, expiry=option_chain.expiry if option_chain else None, legs=(),
            quantity=None, lot_size=None, contracts=None,
            construction_confidence="NONE", construction_status=STATUS_INSUFFICIENT_MARKET_DATA,
            construction_reason="no option leg this cycle had a real, valid bid AND ask -- nothing to price legs against",
            required_evidence=("option_chain.legs[*].bid", "option_chain.legs[*].ask"),
        )

    as_of_date = source_cycle_id[:10] if source_cycle_id else datetime.now(timezone.utc).date().isoformat()

    assessment = construct_trade(
        family, adapted_chain, underlying_price, as_of_date,
        direction=direction, expected_move_pct=expected_move_pct,
        supporting_assessment_ids=strategy_selection.get("supporting_evidence") or (),
        timestamp=source_cycle_id, min_dte=min_dte, max_dte=max_dte,
    )

    required_evidence = ("option_chain", "underlying_price", "market_direction") if family != "CALENDAR" \
        else ("option_chain[near_expiry]", "option_chain[far_expiry]", "underlying_price")

    if not assessment.constructed:
        status = _REJECTION_TO_STATUS.get(assessment.rejection_reason, STATUS_INSUFFICIENT_MARKET_DATA)
        return ShadowTradeCandidate(
            **common_kwargs, expiry=assessment.expiry, legs=(), quantity=None, lot_size=None, contracts=None,
            construction_confidence="NONE", construction_status=status,
            construction_reason=f"{assessment.rejection_reason}: {'; '.join(assessment.explanation.dominant_constraints)}",
            required_evidence=required_evidence,
        )

    # Re-attach real bid/ask (TradeConstructionAssessment only carries the blended mid).
    bid_ask_by_key: Dict[Tuple[float, str], Tuple[Optional[float], Optional[float]]] = {}
    if option_chain is not None:
        for leg in option_chain.legs:
            bid_ask_by_key[(leg.strike, leg.option_type)] = (leg.bid, leg.ask)

    legs: List[ShadowTradeLeg] = []
    all_have_real_quotes = True
    for leg in assessment.legs:
        bid, ask = bid_ask_by_key.get((leg.strike, leg.option_type), (None, None))
        if bid is None or ask is None:
            all_have_real_quotes = False
        legs.append(ShadowTradeLeg(
            role=leg.role, option_type=leg.option_type, strike=leg.strike, expiry=leg.expiry,
            side=leg.side, ratio=leg.ratio, entry_mid=leg.premium, entry_bid=bid, entry_ask=ask,
            open_interest=leg.open_interest, delta=leg.delta, reasoning=leg.reasoning,
        ))

    return ShadowTradeCandidate(
        **common_kwargs, expiry=assessment.expiry, legs=tuple(legs),
        quantity=None, lot_size=None, contracts=None,  # position sizing explicitly out of this slice's scope.
        construction_confidence="HIGH" if all_have_real_quotes else "LOW",
        construction_status=STATUS_CONSTRUCTED,
        construction_reason="; ".join(assessment.explanation.why_these_strikes) or "constructed",
        required_evidence=required_evidence,
    )
