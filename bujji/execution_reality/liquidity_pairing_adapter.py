"""Liquidity Pairing Adapter -- Execution Reality Layer, Phase-1.

PURPOSE: pure structural adapter, converting Phase-0's `LegQuote[]`
into the CE/PE pairs `LiquidityBrain.analyze(ce_bid, ce_ask, pe_bid,
pe_ask)` already requires -- its own real, unmodified signature,
verified by reading `bujji/intelligence/liquidity_brain.py` directly
before writing this file. This module does NOT calculate spreads,
does NOT classify liquidity, does NOT make decisions, does NOT call a
broker, and does NOT know what "trading approval" means. It groups.
That is its entire job.

STRUCTURAL ROLE COMES FROM EXTERNAL METADATA, NEVER FROM LegQuote:
`LegQuote` (Phase-0, frozen) has no `role`/`body`/`wing`/`strategy_leg`
field, and none is added here -- a leg's quote reality (bid/ask/
spread) and a leg's structural role in a particular strategy are
different concerns, decided by different owners, at different times.
The caller (a future, not-yet-built MSI integration -- explicitly
NOT built in this phase) supplies the role mapping; this adapter only
consumes it.

SUPPORTED STRUCTURES: any structure whose legs can be grouped into
CE+PE pairs sharing a role label (short straddle = one pair; iron fly/
iron condor = body pair + wing pair). Call-only or put-only multi-leg
structures (e.g. a call ratio spread) have no natural CE/PE pairing
and are NOT supported by this adapter -- LiquidityBrain.analyze()
itself has no path for them either, and extending it is explicitly
out of this phase's scope (see docs/EXECUTION_INTELLIGENCE_PHASE2_DESIGN.md).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Sequence, Tuple

from bujji.execution_reality.models import LegQuote

OPTION_TYPE_CE = "CE"
OPTION_TYPE_PE = "PE"


class PairingError(Exception):
    """Raised on any structurally invalid role mapping -- never
    silently guessed. A role with anything other than exactly one CE
    and one PE leg is a caller error, surfaced immediately."""


@dataclass(frozen=True)
class LegPair:
    """One CE+PE pair, ready to be handed to LiquidityBrain.analyze()
    verbatim. Carries no spread/classification of its own -- that is
    LiquidityBrain's job, not this adapter's."""

    role_label: str
    ce_leg: LegQuote
    pe_leg: LegQuote


class LiquidityPairingAdapter:
    """Stateless: pair(...) is a pure function of its inputs. No
    broker, no cache, no decision state -- mirrors LiquidityBrain's
    own stated statelessness, matching the layer it sits directly
    beside in this pipeline."""

    def pair(self, legs: Sequence[LegQuote], leg_roles: Dict[str, str]) -> Tuple[LegPair, ...]:
        """`leg_roles`: {symbol: role_label}, caller-supplied, e.g.
        {"NIFTY25000CE": "body", "NIFTY25000PE": "body",
         "NIFTY25500CE": "wing", "NIFTY24500PE": "wing"}.
        Order of `legs` is irrelevant -- pairing is keyed by role_label
        and option_type, never by position."""
        by_role: Dict[str, Dict[str, LegQuote]] = {}
        for leg in legs:
            role = leg_roles.get(leg.symbol)
            if role is None:
                raise PairingError(f"no role supplied for leg {leg.symbol!r} -- every leg must have an explicit role")
            if leg.option_type not in (OPTION_TYPE_CE, OPTION_TYPE_PE):
                raise PairingError(f"leg {leg.symbol!r} has unrecognized option_type {leg.option_type!r}")

            slot = by_role.setdefault(role, {})
            if leg.option_type in slot:
                raise PairingError(
                    f"role {role!r} already has a {leg.option_type} leg ({slot[leg.option_type].symbol!r}) -- "
                    f"cannot also assign {leg.symbol!r} to the same role/option_type; each role must have "
                    f"exactly one CE and one PE leg"
                )
            slot[leg.option_type] = leg

        pairs = []
        for role, slot in by_role.items():
            ce_leg = slot.get(OPTION_TYPE_CE)
            pe_leg = slot.get(OPTION_TYPE_PE)
            if ce_leg is None or pe_leg is None:
                missing = OPTION_TYPE_CE if ce_leg is None else OPTION_TYPE_PE
                raise PairingError(
                    f"role {role!r} is missing its {missing} leg -- LiquidityBrain.analyze() requires "
                    f"exactly one CE and one PE leg per pair; call-only/put-only structures are not "
                    f"supported by this adapter (see this module's own docstring)"
                )
            pairs.append(LegPair(role_label=role, ce_leg=ce_leg, pe_leg=pe_leg))

        return tuple(pairs)
