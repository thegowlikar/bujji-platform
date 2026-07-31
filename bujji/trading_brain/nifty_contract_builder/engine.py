"""NIFTY Contract Builder engine — BUJJI Options OS v3, Engineering
Series 42, Sprint 1 (v1, NIFTY only).

The Trading Brain thinks in strategies. The broker thinks in
contracts. This module performs only that translation, for NIFTY
weekly options, via deterministic templates -- never optimization,
never search, never Greeks, never probability.

Inputs are exactly a `StrategyDecision`, a `CapitalDecision`, a
`NiftySpotSnapshot`, and a `NiftyOptionChainSnapshot` -- never MIC v2,
the Market State Builder, the Risk Brain, a broker SDK, or the Runtime
Execution Service. This module constructs contracts; it never places
an order, calculates a lot size, calculates margin, authenticates, or
monitors a fill.

Construction is atomic: if any leg of a strategy's template cannot be
matched to a real entry in the supplied option chain, the whole
construction fails and zero contracts are returned -- never a partial,
fabricated result.
"""
from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Dict, List, Optional, Tuple

from ..capital_brain.models import CapitalDecision
from ..strategy_selector.models import StrategyDecision
from . import taxonomy
from .models import (
    ContractConstructionResult,
    NiftyOptionChainEntry,
    NiftyOptionChainSnapshot,
    NiftyOptionContract,
    NiftySpotSnapshot,
)

Clock = Callable[[], datetime]


def _real_clock() -> datetime:
    return datetime.now()


@dataclass(frozen=True)
class _LegTemplate:
    option_type: str
    moneyness: str
    side: str
    week_offset: int = 0  # 0 = nearest weekly ("current week"), 1 = next weekly


# ---------------------------------------------------------------------------
# v1 template registry. Deterministic, finite, never scored or
# searched. Adding a strategy means adding one entry here -- nothing
# else in this file changes.
# ---------------------------------------------------------------------------
TEMPLATES: Dict[str, Tuple[_LegTemplate, ...]] = {
    "PREMIUM_VWAP_STRADDLE": (
        _LegTemplate(taxonomy.OPTION_TYPE_CE, taxonomy.MONEYNESS_ATM, taxonomy.SIDE_SELL),
        _LegTemplate(taxonomy.OPTION_TYPE_PE, taxonomy.MONEYNESS_ATM, taxonomy.SIDE_SELL),
    ),
    "IRON_FLY": (
        _LegTemplate(taxonomy.OPTION_TYPE_CE, taxonomy.MONEYNESS_ATM, taxonomy.SIDE_SELL),
        _LegTemplate(taxonomy.OPTION_TYPE_PE, taxonomy.MONEYNESS_ATM, taxonomy.SIDE_SELL),
        _LegTemplate(taxonomy.OPTION_TYPE_CE, taxonomy.MONEYNESS_OTM1, taxonomy.SIDE_BUY),
        _LegTemplate(taxonomy.OPTION_TYPE_PE, taxonomy.MONEYNESS_OTM1, taxonomy.SIDE_BUY),
    ),
    "IRON_CONDOR": (
        _LegTemplate(taxonomy.OPTION_TYPE_CE, taxonomy.MONEYNESS_OTM1, taxonomy.SIDE_SELL),
        _LegTemplate(taxonomy.OPTION_TYPE_CE, taxonomy.MONEYNESS_OTM2, taxonomy.SIDE_BUY),
        _LegTemplate(taxonomy.OPTION_TYPE_PE, taxonomy.MONEYNESS_OTM1, taxonomy.SIDE_SELL),
        _LegTemplate(taxonomy.OPTION_TYPE_PE, taxonomy.MONEYNESS_OTM2, taxonomy.SIDE_BUY),
    ),
    "DIRECTIONAL_CALL_SPREAD": (
        _LegTemplate(taxonomy.OPTION_TYPE_CE, taxonomy.MONEYNESS_ATM, taxonomy.SIDE_BUY),
        _LegTemplate(taxonomy.OPTION_TYPE_CE, taxonomy.MONEYNESS_OTM1, taxonomy.SIDE_SELL),
    ),
    "DIRECTIONAL_PUT_SPREAD": (
        _LegTemplate(taxonomy.OPTION_TYPE_PE, taxonomy.MONEYNESS_ATM, taxonomy.SIDE_BUY),
        _LegTemplate(taxonomy.OPTION_TYPE_PE, taxonomy.MONEYNESS_OTM1, taxonomy.SIDE_SELL),
    ),
    "CALENDAR_SPREAD": (
        _LegTemplate(taxonomy.OPTION_TYPE_CE, taxonomy.MONEYNESS_ATM, taxonomy.SIDE_SELL, week_offset=0),
        _LegTemplate(taxonomy.OPTION_TYPE_CE, taxonomy.MONEYNESS_ATM, taxonomy.SIDE_BUY, week_offset=1),
    ),
}


def _atm_strike(spot: float) -> int:
    """Deterministic floor-based midpoint-up rounding to the nearest
    strike interval -- mirrors the rounding convention documented in
    production's own `Broker.atm_strike()` (Series 41 audit), chosen
    for the same reason: Python's default banker's rounding is not
    deterministic in the way a strike-selection policy requires.
    """
    return int(math.floor(spot / taxonomy.STRIKE_INTERVAL + 0.5) * taxonomy.STRIKE_INTERVAL)


def _strike_for_leg(atm: int, option_type: str, moneyness: str) -> int:
    interval = taxonomy.STRIKE_INTERVAL
    if moneyness == taxonomy.MONEYNESS_ATM:
        return atm
    if moneyness == taxonomy.MONEYNESS_ATM_PLUS_1:
        return atm + interval
    if moneyness == taxonomy.MONEYNESS_ATM_MINUS_1:
        return atm - interval
    if moneyness == taxonomy.MONEYNESS_OTM1:
        return atm + interval if option_type == taxonomy.OPTION_TYPE_CE else atm - interval
    if moneyness == taxonomy.MONEYNESS_OTM2:
        return atm + 2 * interval if option_type == taxonomy.OPTION_TYPE_CE else atm - 2 * interval
    raise ValueError(f"Unrecognized moneyness '{moneyness}'")


def _find_entry(
    chain: NiftyOptionChainSnapshot, strike: int, option_type: str, expiry: str
):
    for entry in chain.entries:
        if entry.strike == strike and entry.option_type == option_type and entry.expiry == expiry:
            return entry
    return None


def _failure(
    reason: str,
    strategy_id: Optional[str],
    capital_intent: str,
    spot: Optional[float],
    atm: Optional[int],
    timestamp: str,
) -> ContractConstructionResult:
    seed = "|".join([strategy_id or "NONE", reason, timestamp])
    construction_id = "CCR-" + hashlib.md5(seed.encode()).hexdigest()[:16]
    trace = f"FAILED because {reason}."
    return ContractConstructionResult(
        construction_id=construction_id,
        status=taxonomy.CONSTRUCTION_STATUS_FAILED,
        strategy_id=strategy_id,
        capital_intent=capital_intent,
        contracts=(),
        failure_reason=reason,
        construction_trace=trace,
        spot_used=spot,
        atm_strike_used=atm,
        timestamp=timestamp,
        version=taxonomy.NIFTY_CONTRACT_BUILDER_VERSION,
    )


def build_contracts(
    strategy_decision: Optional[StrategyDecision],
    capital_decision: Optional[CapitalDecision],
    spot_snapshot: Optional[NiftySpotSnapshot],
    option_chain: Optional[NiftyOptionChainSnapshot],
    clock: Clock = _real_clock,
) -> ContractConstructionResult:
    """Translate a Trading Brain decision into concrete NIFTY option
    contracts, or fail honestly.

    Never places an order, never calculates a lot size or margin,
    never uses a Greek, a probability, or randomness -- only matches a
    deterministic per-strategy leg template against a real, supplied
    option chain snapshot.
    """
    timestamp = clock().isoformat()

    # Rule: insufficient upstream data.
    if (
        strategy_decision is None
        or strategy_decision.selected_strategy is None
        or capital_decision is None
    ):
        return _failure(
            taxonomy.FAILURE_REASON_INSUFFICIENT_DATA,
            strategy_decision.selected_strategy if strategy_decision else None,
            capital_decision.capital_intent if capital_decision else "UNKNOWN",
            None,
            None,
            timestamp,
        )

    strategy_id = strategy_decision.selected_strategy
    capital_intent = capital_decision.capital_intent

    # Rule: strategy has no v1 contract template. Two distinct cases,
    # per Engineering Series 63 -- a strategy the Strategy Selector's
    # own registry recognizes but v1 deliberately does not template
    # (a disclosed scope boundary) is a different, expected outcome
    # from a strategy the registry itself has never heard of (a
    # genuine anomaly). Conflating them under one failure reason is
    # exactly what let Historical Qualification Campaign v2 mistake a
    # documented boundary for an unexplained defect.
    if strategy_id not in TEMPLATES:
        if strategy_id in taxonomy.UNSUPPORTED_REGISTERED_STRATEGIES:
            return _failure(
                taxonomy.FAILURE_REASON_STRATEGY_OUT_OF_V1_SCOPE,
                strategy_id,
                capital_intent,
                None,
                None,
                timestamp,
            )
        return _failure(
            taxonomy.FAILURE_REASON_UNKNOWN_STRATEGY,
            strategy_id,
            capital_intent,
            None,
            None,
            timestamp,
        )

    # Rule: invalid spot.
    if spot_snapshot is None or spot_snapshot.spot is None or spot_snapshot.spot <= 0:
        return _failure(
            taxonomy.FAILURE_REASON_INVALID_SPOT,
            strategy_id,
            capital_intent,
            None,
            None,
            timestamp,
        )
    spot = spot_snapshot.spot
    atm = _atm_strike(spot)

    # Rule: missing option chain.
    if option_chain is None:
        return _failure(
            taxonomy.FAILURE_REASON_MISSING_OPTION_CHAIN,
            strategy_id,
            capital_intent,
            spot,
            atm,
            timestamp,
        )

    template = TEMPLATES[strategy_id]
    weeks_needed = max(leg.week_offset for leg in template) + 1

    # Rule: no weekly expiry (or not enough for a multi-expiry template).
    if len(option_chain.expiries) < weeks_needed:
        return _failure(
            taxonomy.FAILURE_REASON_NO_WEEKLY_EXPIRY,
            strategy_id,
            capital_intent,
            spot,
            atm,
            timestamp,
        )

    # Construction is atomic: resolve every leg before committing to
    # any of them.
    resolved: List[Tuple[_LegTemplate, int, str, NiftyOptionChainEntry]] = []
    for leg in template:
        strike = _strike_for_leg(atm, leg.option_type, leg.moneyness)
        expiry = option_chain.expiries[leg.week_offset]
        entry = _find_entry(option_chain, strike, leg.option_type, expiry)
        if entry is None:
            return _failure(
                taxonomy.FAILURE_REASON_NO_MATCHING_STRIKE,
                strategy_id,
                capital_intent,
                spot,
                atm,
                timestamp,
            )
        resolved.append((leg, strike, expiry, entry))

    contracts: List[NiftyOptionContract] = []
    leg_summaries: List[str] = []
    for index, (leg, strike, expiry, entry) in enumerate(resolved):
        selection_reason = (
            f"{leg.moneyness} {leg.side} {leg.option_type} at strike {strike} "
            f"(spot {spot}, ATM {atm}), expiry {expiry}, matched chain symbol {entry.contract_symbol}."
        )
        seed = "|".join(
            [
                strategy_decision.decision_id,
                str(index),
                str(strike),
                leg.option_type,
                leg.side,
                expiry,
                timestamp,
            ]
        )
        contract_id = "NC-" + hashlib.md5(seed.encode()).hexdigest()[:16]
        contract = NiftyOptionContract(
            contract_id=contract_id,
            underlying=taxonomy.UNDERLYING_NIFTY,
            expiry=expiry,
            strike=strike,
            option_type=leg.option_type,
            side=leg.side,
            contract_symbol=entry.contract_symbol,
            capital_intent=capital_intent,
            strategy_id=strategy_id,
            selection_reason=selection_reason,
            construction_trace=selection_reason,
            timestamp=timestamp,
            version=taxonomy.NIFTY_CONTRACT_BUILDER_VERSION,
            last_price=entry.last_price,
        )
        contracts.append(contract)
        leg_summaries.append(f"{strike}{leg.option_type}")

    overall_trace = (
        f"Strategy {strategy_id}. Spot {spot}. ATM Strike {atm}. "
        f"Weekly Expiry {resolved[0][2]}. Constructed: {', '.join(leg_summaries)}."
    )
    seed = "|".join([strategy_decision.decision_id, strategy_id, timestamp])
    construction_id = "CCR-" + hashlib.md5(seed.encode()).hexdigest()[:16]

    return ContractConstructionResult(
        construction_id=construction_id,
        status=taxonomy.CONSTRUCTION_STATUS_CONSTRUCTED,
        strategy_id=strategy_id,
        capital_intent=capital_intent,
        contracts=tuple(contracts),
        failure_reason=None,
        construction_trace=overall_trace,
        spot_used=spot,
        atm_strike_used=atm,
        timestamp=timestamp,
        version=taxonomy.NIFTY_CONTRACT_BUILDER_VERSION,
    )
