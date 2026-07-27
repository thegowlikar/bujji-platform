"""Trade Construction Foundation models — Series 90.

Frozen dataclasses throughout (house convention). `TradeConstructionAssessment`
is the top-level immutable output; `constructed=False` + `rejection_reason`
is a genuine, honest outcome (Deliverable 5, fail closed), never an
exception -- exactly like `msi_strategy_selector`'s `selected_strategy_family
= None` before it.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Optional, Tuple


@dataclass(frozen=True)
class ExpiryDecision:
    """Deliverable 2 output. `chosen_expiry is None` is a real, honest
    outcome when no candidate expiry survives the DTE window."""
    chosen_expiry: Optional[str]
    dte: Optional[int]
    candidate_expiries: Tuple[str, ...]
    rejected_expiries: Tuple[Tuple[str, str], ...]  # (expiry, rejection_reason)
    reasoning: Tuple[str, ...]


@dataclass(frozen=True)
class StrikeLeg:
    """One leg of the constructed position. `delta`/`premium`/
    `open_interest` are the real observed evidence used to select this
    exact strike -- always cited in `reasoning`, never a historical
    return figure."""
    role: str            # taxonomy.ALL_STRIKE_ROLES
    option_type: str     # "CE" | "PE"
    strike: float
    expiry: str
    delta: Optional[float]
    premium: Optional[float]
    open_interest: Optional[float]
    side: str            # "BUY" | "SELL"
    ratio: int            # Contract multiplier on this leg (e.g. 2 for a
                           # butterfly's short body) -- always relative to
                           # 1 unit on the structure's other leg(s), never
                           # a position-sizing decision (that is Position
                           # Construction's job, out of this package's scope).
    reasoning: Tuple[str, ...]


@dataclass(frozen=True)
class Explanation:
    assessment_id: str
    why_this_expiry: Tuple[str, ...]
    why_these_strikes: Tuple[str, ...]
    why_not_neighbouring_strikes: Tuple[str, ...]
    dominant_constraints: Tuple[str, ...]
    schema_version: str


@dataclass(frozen=True)
class TradeConstructionAssessment:
    assessment_id: str
    timestamp: str
    strategy_family: str
    constructed: bool
    rejection_reason: Optional[str]     # taxonomy.ALL_REJECTION_REASONS, set iff not constructed.
    expiry: Optional[str]
    expiry_decision: ExpiryDecision
    legs: Tuple[StrikeLeg, ...]
    entry_reference_prices: Mapping[str, float]   # keyed by f"{option_type}_{strike}"
    expected_credit_debit: Optional[float]        # positive = net credit, negative = net debit.
    risk_profile: str                             # taxonomy.ALL_RISK_PROFILES.
    required_margin: Optional[float]              # Always None in this package (Deliverable 1
                                                   # finding: no deterministic/replay-safe margin
                                                   # source exists -- only a LIVE broker SPAN-margin
                                                   # call, see docs). Honestly disclosed, not guessed.
    margin_unavailable_reason: Optional[str]
    supporting_assessment_ids: Tuple[str, ...]    # Upstream SSF/MSS assessment_ids this construction used.
    explanation: Explanation
    provenance: str
    schema_version: str
