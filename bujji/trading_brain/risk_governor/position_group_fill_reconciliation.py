"""Fill reconciliation — BUJJI Options OS v3, Numeric Risk Governor
Gate A.

Computes the positive-only quantity/cost delta for a FILL_OBSERVED
event from a broker's reported CUMULATIVE figures, against the fold's
own last-recorded cumulative figures for that client_order_id -- never
a locally-cached value, so repeated calls after any number of restarts
are safe: each call re-diffs against what's already durably recorded
and only ever appends the new portion. See position_group_fold.py for
how the resulting event affects a group's derived state.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .position_group_fold import LegFillState

COST_BASIS_DERIVED = "DERIVED"
COST_BASIS_UNTRUSTED = "UNTRUSTED_INSUFFICIENT_PRICE_DATA"


class NonMonotonicFillReport(Exception):
    """Raised when a broker reports LESS cumulative fill than the
    journal already durably recorded -- a real broker never reverses a
    fill, so this indicates a genuine anomaly. Never silently applied
    in either direction (never truncated to the lower figure, never
    trusted as an inflation)."""


@dataclass(frozen=True)
class FillDelta:
    """None fields mean exactly what they say -- never defaulted."""

    delta_quantity: int
    delta_value: Optional[float]
    cost_basis_status: str


def compute_fill_delta(
    prior: LegFillState,
    cumulative_filled_quantity_after: int,
    cumulative_average_fill_price_after: Optional[float],
) -> FillDelta:
    delta_quantity = cumulative_filled_quantity_after - prior.cumulative_filled_quantity
    if delta_quantity < 0:
        raise NonMonotonicFillReport(
            f"broker reported cumulative_filled_quantity_after="
            f"{cumulative_filled_quantity_after}, journal already has "
            f"{prior.cumulative_filled_quantity} for {prior.client_order_id}"
        )

    prior_price = prior.cumulative_average_fill_price
    new_price = cumulative_average_fill_price_after

    if prior_price is None and prior.cumulative_filled_quantity == 0:
        # First fill for this leg: "prior cumulative value" is simply 0,
        # no prior price needed to derive it.
        prior_value = 0.0
    elif prior_price is not None:
        prior_value = prior.cumulative_filled_quantity * prior_price
    else:
        prior_value = None  # prior fills exist but with no known price -- cost basis is unrecoverable

    if new_price is not None and prior_value is not None:
        new_value = cumulative_filled_quantity_after * new_price
        delta_value = new_value - prior_value
        status = COST_BASIS_DERIVED
    else:
        delta_value = None
        status = COST_BASIS_UNTRUSTED

    return FillDelta(delta_quantity=delta_quantity, delta_value=delta_value, cost_basis_status=status)
