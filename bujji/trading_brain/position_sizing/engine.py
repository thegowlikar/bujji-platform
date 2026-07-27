"""Position Sizing Engine engine — BUJJI Options OS v3, Engineering
Series 43, Sprint 1 (v1).

The Trading Brain decides whether to trade. The NIFTY Contract Builder
decides what to trade. This module decides how much to trade -- the
final business decision before runtime execution. It never chooses a
strategy, never chooses a strike, never places an order, and never
communicates with a broker.

Inputs are exactly a `CapitalDecision`, a tuple of already-constructed
`NiftyOptionContract`s, a `CapitalPolicy`, and a `LotSpecification` --
nothing else. Quantity is always `lots * lot_size`, where `lots` comes
solely from `PositionSizingConfig`'s finite lot table keyed by
`capital_intent` -- never from emotion, confidence, prediction,
leverage, margin, or exposure modeling.

Multi-leg rule: every leg of one strategy receives the identical lot
count. Legs are never sized independently.
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Callable, Optional, Tuple

from ..capital_brain.models import CapitalDecision
from ..nifty_contract_builder.models import NiftyOptionContract
from . import taxonomy
from .config import PositionSizingConfig
from .models import CapitalPolicy, LotSpecification, PositionPlan

Clock = Callable[[], datetime]


def _real_clock() -> datetime:
    return datetime.now()


_LOTS_BY_INTENT_FIELD = {
    "MINIMAL": "minimum_lots",
    "REDUCED": "reduced_lots",
    "STANDARD": "standard_lots",
    "FULL": "full_lots",
}


def _config_is_valid(cfg: PositionSizingConfig) -> bool:
    if cfg.minimum_lots <= 0 or cfg.reduced_lots <= 0 or cfg.standard_lots <= 0 or cfg.full_lots <= 0:
        return False
    if cfg.max_lots <= 0:
        return False
    if cfg.full_lots > cfg.max_lots:
        return False
    return True


def _has_duplicate_legs(contracts: Tuple[NiftyOptionContract, ...]) -> bool:
    keys = [(c.strike, c.option_type, c.expiry, c.side) for c in contracts]
    return len(set(keys)) != len(keys)


def _failure(
    reason: str,
    capital_decision: Optional[CapitalDecision],
    contracts: Tuple[NiftyOptionContract, ...],
    timestamp: str,
) -> PositionPlan:
    capital_intent = capital_decision.capital_intent if capital_decision else "UNKNOWN"
    seed = "|".join(
        [capital_decision.decision_id if capital_decision else "NONE", reason, timestamp]
    )
    plan_id = "PP-" + hashlib.md5(seed.encode()).hexdigest()[:16]
    trace = f"FAILED because {reason}."
    return PositionPlan(
        plan_id=plan_id,
        contracts=contracts,
        lots_per_leg=0,
        quantity_per_leg=0,
        capital_intent=capital_intent,
        sizing_policy="UNKNOWN",
        validation=taxonomy.VALIDATION_STATUS_FAILED,
        sizing_reason=trace,
        sizing_trace=trace,
        failure_reason=reason,
        capital_decision_id=capital_decision.decision_id if capital_decision else None,
        timestamp=timestamp,
        version=taxonomy.POSITION_SIZING_VERSION,
    )


def size_position(
    capital_decision: Optional[CapitalDecision],
    contracts: Optional[Tuple[NiftyOptionContract, ...]],
    capital_policy: Optional[CapitalPolicy],
    lot_spec: Optional[LotSpecification],
    sizing_config: PositionSizingConfig,
    clock: Clock = _real_clock,
) -> PositionPlan:
    """Convert an authorized capital intent into a validated, uniform
    per-leg quantity.

    Never calculates broker margin, never models exposure, never
    applies leverage, and never sizes one leg differently from
    another belonging to the same strategy.
    """
    timestamp = clock().isoformat()

    # Rule: nothing usable to size from at all.
    if (
        capital_decision is None
        or contracts is None
        or capital_policy is None
        or lot_spec is None
    ):
        return _failure(
            taxonomy.FAILURE_REASON_INSUFFICIENT_DATA,
            capital_decision,
            contracts or (),
            timestamp,
        )

    capital_intent = capital_decision.capital_intent

    # Rule: unrecognized capital intent.
    if capital_intent not in taxonomy.REUSED_CAPITAL_INTENTS:
        return _failure(taxonomy.FAILURE_REASON_UNKNOWN_CAPITAL_INTENT, capital_decision, contracts, timestamp)

    # Rule: invalid capital policy or sizing configuration.
    if capital_policy.policy not in taxonomy.ALL_CAPITAL_POLICIES:
        return _failure(taxonomy.FAILURE_REASON_INVALID_CONFIGURATION, capital_decision, contracts, timestamp)
    if not _config_is_valid(sizing_config):
        return _failure(taxonomy.FAILURE_REASON_INVALID_CONFIGURATION, capital_decision, contracts, timestamp)

    # Rule: invalid lot specification.
    if (
        lot_spec.underlying != taxonomy.UNDERLYING_NIFTY
        or lot_spec.lot_size <= 0
    ):
        return _failure(taxonomy.FAILURE_REASON_INVALID_LOT_SPECIFICATION, capital_decision, contracts, timestamp)

    # Rule: empty contract set.
    if len(contracts) == 0:
        return _failure(taxonomy.FAILURE_REASON_EMPTY_CONTRACT_SET, capital_decision, contracts, timestamp)

    # Rule: duplicate legs make sizing ambiguous.
    if _has_duplicate_legs(contracts):
        return _failure(taxonomy.FAILURE_REASON_INSUFFICIENT_DATA, capital_decision, contracts, timestamp)

    # Determine lots for this capital intent. NONE always maps to zero
    # lots -- an honest "no position", never a fabricated size.
    if capital_intent == "NONE" or capital_intent == "UNKNOWN":
        lots = 0
        sizing_policy = capital_intent
    else:
        field_name = _LOTS_BY_INTENT_FIELD[capital_intent]
        lots = getattr(sizing_config, field_name)
        sizing_policy = capital_intent

    quantity = lots * lot_spec.lot_size

    # Rule: zero quantity is a valid, honestly-reported outcome, never
    # fabricated as a real position.
    if quantity <= 0:
        return _failure(taxonomy.FAILURE_REASON_ZERO_QUANTITY, capital_decision, contracts, timestamp)

    leg_summary = ", ".join(f"{c.strike}{c.option_type}" for c in contracts)
    trace = (
        f"Capital Intent {capital_intent}. Configured Lots {lots}. "
        f"Lot Size {lot_spec.lot_size}. Quantity {quantity}. Applied to {leg_summary}."
    )

    seed = "|".join([capital_decision.decision_id, capital_intent, str(lots), timestamp])
    plan_id = "PP-" + hashlib.md5(seed.encode()).hexdigest()[:16]

    return PositionPlan(
        plan_id=plan_id,
        contracts=contracts,
        lots_per_leg=lots,
        quantity_per_leg=quantity,
        capital_intent=capital_intent,
        sizing_policy=sizing_policy,
        validation=taxonomy.VALIDATION_STATUS_PASSED,
        sizing_reason=trace,
        sizing_trace=trace,
        failure_reason=None,
        capital_decision_id=capital_decision.decision_id,
        timestamp=timestamp,
        version=taxonomy.POSITION_SIZING_VERSION,
    )
