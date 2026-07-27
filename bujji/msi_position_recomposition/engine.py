"""bujji.msi_position_recomposition.engine — Series 110.

Composes, read-only, never re-derives:
  - `bujji.msi_dynamic_management` (Series 109, frozen) -- which
    decision fires and at what priority (`query.highest_priority_decision`,
    `DynamicManagementBoard.transition`).
  - `bujji.msi_strategy_optimization` (Series 108, frozen) -- family
    delta targets (`FAMILY_DELTA_TARGETS`, reused via `msi_trade_construction
    .config`, the same source Series 108 itself reuses).
  - `bujji.msi_trade_construction.engine.construct_trade` (Series 90,
    frozen) -- the REAL, exact, per-family strike/expiry/leg builder.
    This is the single most important reuse decision in this package:
    rather than re-deriving a second, approximate leg-construction
    algorithm (risking silent divergence from Series 90's own real,
    validated logic), every new position in this package comes from a
    genuine `construct_trade` call against the real current chain. The
    "recomposition" this package adds is entirely in DIFFING that real
    new position against the currently-held real legs (Deliverable 4)
    and packaging the result as a directly execution-planning-consumable
    delta (Deliverable 6) -- never in reinventing strike/expiry/wing logic.

This module adds ZERO new market-data parsing and ZERO new strike/
expiry/wing selection logic.
"""
from __future__ import annotations

import hashlib
from datetime import date
from typing import Any, Optional, Sequence, Tuple

from bujji.msi_dynamic_management import query as mdm_query
from bujji.msi_dynamic_management import taxonomy as mdm_taxonomy
from bujji.msi_trade_construction import engine as tc_engine
from bujji.msi_trade_construction import config as tc_config

from . import config as _config
from . import taxonomy
from .models import Explanation, CloseLeg, ExecutionDelta, RecompositionAssessment


def _explain(seed: str, *, why_strike, why_expiry, why_width, why_keep, why_replace, why_not_rebuild) -> Explanation:
    aid = hashlib.md5(f"{seed}|{taxonomy.MSI_POSITION_RECOMPOSITION_VERSION}".encode()).hexdigest()
    return Explanation(
        assessment_id=aid, why_this_strike=tuple(why_strike), why_this_expiry=tuple(why_expiry),
        why_this_width=tuple(why_width), why_keep_this_leg=tuple(why_keep), why_replace_that_leg=tuple(why_replace),
        why_not_rebuild_everything=tuple(why_not_rebuild), schema_version=taxonomy.MSI_POSITION_RECOMPOSITION_VERSION,
    )


def _not_possible(reason: str, *, trigger: Optional[str], from_family: str, timestamp: str) -> RecompositionAssessment:
    explanation = _explain(
        f"NOTPOSSIBLE|{from_family}|{trigger}|{reason}|{timestamp}",
        why_strike=[f"RECOMPOSITION_NOT_POSSIBLE: {reason}"], why_expiry=[reason], why_width=[reason],
        why_keep=["not applicable -- no new position was produced"],
        why_replace=["not applicable -- no new position was produced"],
        why_not_rebuild=["not applicable -- recomposition did not proceed"],
    )
    aid = hashlib.md5(f"MPR|{from_family}|{trigger}|NOT_POSSIBLE|{timestamp}".encode()).hexdigest()
    return RecompositionAssessment(
        assessment_id=aid, possible=False, reason_not_possible=reason, trigger=trigger, from_family=from_family,
        to_family=None, new_expiry=None, target_delta=None, wing_width=None, expected_move=None,
        execution_delta=None, strike_replacements=0, expiry_replacements=0, legs_reused=0, full_rebuild=False,
        timestamp=timestamp, explanation=explanation, provenance=_config.DEFAULT_PROVENANCE, schema_version=_config.SCHEMA_VERSION,
    )


def _leg_key(option_type: str, strike: float, expiry: str, side: str) -> Tuple:
    return (option_type, strike, expiry, side)


def _wing_width_from_legs(legs: Sequence) -> Optional[float]:
    by_type: dict = {}
    for leg in legs:
        by_type.setdefault(leg.option_type, []).append(leg.strike)
    widths = [max(strikes) - min(strikes) for strikes in by_type.values() if len(strikes) >= 2]
    return max(widths) if widths else None


def recompose_position(
    board: Any, held_legs: Sequence, strategy_family: str, chain: Sequence, spot: Optional[float], day: str,
    *, direction: Optional[str] = None, expected_move_pct: Optional[float] = None, timestamp: str,
) -> RecompositionAssessment:
    """Deliverable 3/4/5/6. `board` is a real `DynamicManagementBoard`
    (Series 109, frozen); `held_legs` are the position's real current
    `HeldLeg`s (Series 91)."""
    top = mdm_query.highest_priority_decision(board)
    if not top.recommended or top.priority not in (mdm_taxonomy.PRIORITY_MANDATORY, mdm_taxonomy.PRIORITY_RECOMMENDED):
        return _not_possible(
            f"no actionable Series 109 decision -- highest priority is {top.decision_type}={top.priority}, "
            "neither MANDATORY nor RECOMMENDED",
            trigger=None, from_family=strategy_family, timestamp=timestamp,
        )
    trigger = top.decision_type

    if trigger == mdm_taxonomy.DECISION_FULL_EXIT:
        close_legs = tuple(CloseLeg(option_type=l.option_type, strike=l.strike, expiry=l.expiry, side=l.side) for l in held_legs)
        execution_delta = ExecutionDelta(close_legs=close_legs, open_legs=(), kept_legs=())
        explanation = _explain(
            f"EXIT|{strategy_family}|{timestamp}",
            why_strike=["full exit -- no new strikes are being selected"],
            why_expiry=["full exit -- no new expiry is being selected"],
            why_width=["full exit -- no new wing width applies"],
            why_keep=["no legs are kept -- exit closes the entire position"],
            why_replace=[f"all {len(held_legs)} held leg(s) are closed: {top.explanation.why}"],
            why_not_rebuild=["not applicable -- exit is a close, not a rebuild"],
        )
        aid = hashlib.md5(f"MPR|{strategy_family}|EXIT|{timestamp}".encode()).hexdigest()
        return RecompositionAssessment(
            assessment_id=aid, possible=True, reason_not_possible=None, trigger=trigger, from_family=strategy_family,
            to_family=None, new_expiry=None, target_delta=None, wing_width=None, expected_move=None,
            execution_delta=execution_delta, strike_replacements=0, expiry_replacements=0, legs_reused=0,
            full_rebuild=False, timestamp=timestamp, explanation=explanation,
            provenance=_config.DEFAULT_PROVENANCE, schema_version=_config.SCHEMA_VERSION,
        )

    to_family = strategy_family
    if trigger == mdm_taxonomy.DECISION_STRATEGY_CONVERSION:
        if not board.transition.representable:
            return _not_possible(
                f"Series 109's own transition assessment reports NOT representable: {board.transition.reasoning[0]}",
                trigger=trigger, from_family=strategy_family, timestamp=timestamp,
            )
        to_family = board.transition.to_family

    if not held_legs and trigger != mdm_taxonomy.DECISION_STRATEGY_CONVERSION:
        return _not_possible("no held legs supplied -- nothing to recompose", trigger=trigger, from_family=strategy_family, timestamp=timestamp)

    construct_kwargs = {}
    if trigger == mdm_taxonomy.DECISION_EXPIRY_ROLL and held_legs:
        current_expiries = {l.expiry for l in held_legs}
        min_expiry_dte = min((date.fromisoformat(e) - date.fromisoformat(day)).days for e in current_expiries)
        construct_kwargs["min_dte"] = max(min_expiry_dte + _config.EXPIRY_ROLL_MIN_DTE_BUFFER, tc_config.DEFAULT_MIN_DTE)

    if chain is None or spot is None:
        return _not_possible("no real chain/spot available -- cannot re-invoke Trade Construction", trigger=trigger, from_family=strategy_family, timestamp=timestamp)

    tc = tc_engine.construct_trade(
        to_family, chain, spot, day, direction=direction, expected_move_pct=expected_move_pct,
        timestamp=timestamp, **construct_kwargs,
    )
    if not tc.constructed:
        return _not_possible(
            f"Trade Construction (Series 90) could not construct {to_family}: {tc.rejection_reason}",
            trigger=trigger, from_family=strategy_family, timestamp=timestamp,
        )

    old_keys = {_leg_key(l.option_type, l.strike, l.expiry, l.side) for l in held_legs}
    new_keys = {_leg_key(l.option_type, l.strike, l.expiry, l.side) for l in tc.legs}

    kept_new = tuple(l for l in tc.legs if _leg_key(l.option_type, l.strike, l.expiry, l.side) in old_keys)
    opened_new = tuple(l for l in tc.legs if _leg_key(l.option_type, l.strike, l.expiry, l.side) not in old_keys)
    closed_old = tuple(
        CloseLeg(option_type=l.option_type, strike=l.strike, expiry=l.expiry, side=l.side)
        for l in held_legs if _leg_key(l.option_type, l.strike, l.expiry, l.side) not in new_keys
    )
    kept_as_close_shape = tuple(CloseLeg(option_type=l.option_type, strike=l.strike, expiry=l.expiry, side=l.side) for l in kept_new)

    execution_delta = ExecutionDelta(close_legs=closed_old, open_legs=opened_new, kept_legs=kept_as_close_shape)
    full_rebuild = len(kept_new) == 0 and len(held_legs) > 0
    expiry_replacements = 1 if (held_legs and any(l.expiry != tc.expiry for l in held_legs)) else 0
    wing_width = _wing_width_from_legs(tc.legs)
    expected_move = (expected_move_pct * spot) if (expected_move_pct is not None and spot is not None) else None
    target_delta = tc_config.FAMILY_DELTA_TARGETS.get(to_family)

    keep_reasons = [f"leg {l.option_type} {l.strike} (expiry={l.expiry}, side={l.side}) is unchanged by the real "
                    f"re-construction of {to_family} -- its strike/expiry already achieves the family's target evidence"
                    for l in kept_new] or ["no legs were kept"]
    replace_reasons = [f"leg {l.option_type} {l.strike} (expiry={l.expiry}, side={l.side}) is closed -- the real "
                       f"re-construction of {to_family} no longer selects this exact strike/expiry"
                       for l in closed_old] or ["no legs required replacement"]

    explanation = _explain(
        f"MPR|{to_family}|{tc.assessment_id}|{timestamp}",
        why_strike=list(tc.explanation.why_these_strikes) or [f"see Trade Construction's own real strike selection for {to_family}"],
        why_expiry=list(tc.explanation.why_this_expiry) or [f"see Trade Construction's own expiry selection: {tc.expiry}"],
        why_width=[f"wing_width={wing_width}"] if wing_width is not None else ["no multi-strike-per-side wing exists in this family's real construction"],
        why_keep=keep_reasons, why_replace=replace_reasons,
        why_not_rebuild=([f"{len(kept_new)}/{len(tc.legs)} new leg(s) already matched an existing held leg exactly -- "
                          "closing and reopening an unchanged leg would be needless churn with real transaction cost"]
                         if kept_new else
                         [f"none of the real re-constructed legs matched any held leg exactly -- a full rebuild is "
                          "the honest outcome of the diff, not a default choice"]),
    )
    aid = hashlib.md5(f"MPR|{to_family}|{tc.assessment_id}|{trigger}|{timestamp}".encode()).hexdigest()
    return RecompositionAssessment(
        assessment_id=aid, possible=True, reason_not_possible=None, trigger=trigger, from_family=strategy_family,
        to_family=to_family, new_expiry=tc.expiry, target_delta=target_delta, wing_width=wing_width,
        expected_move=expected_move, execution_delta=execution_delta, strike_replacements=len(closed_old),
        expiry_replacements=expiry_replacements, legs_reused=len(kept_new), full_rebuild=full_rebuild,
        timestamp=timestamp, explanation=explanation, provenance=_config.DEFAULT_PROVENANCE, schema_version=_config.SCHEMA_VERSION,
    )
