"""NIFTY Contract Builder models — frozen, immutable records.

Nothing here calculates a lot size, a margin figure, a Greek, or a
probability. `NiftyOptionContract` names a real, concrete option
contract found in a supplied option chain snapshot -- it is never
fabricated when no matching entry exists.

`NiftySpotSnapshot` and `NiftyOptionChainSnapshot` are the two "live
input" types this module accepts. They are plain, broker-neutral data
carriers this module never fetches itself -- the caller supplies them,
exactly like every other Trading Brain module receives its inputs
already produced.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class NiftySpotSnapshot:
    spot: Optional[float]
    as_of: str


@dataclass(frozen=True)
class NiftyOptionChainEntry:
    strike: int
    option_type: str
    expiry: str
    contract_symbol: str


@dataclass(frozen=True)
class NiftyOptionChainSnapshot:
    expiries: Tuple[str, ...]
    entries: Tuple[NiftyOptionChainEntry, ...]
    as_of: str


@dataclass(frozen=True)
class NiftyOptionContract:
    contract_id: str
    underlying: str
    expiry: str
    strike: int
    option_type: str
    side: str
    contract_symbol: str
    capital_intent: str
    strategy_id: str
    selection_reason: str
    construction_trace: str
    timestamp: str
    version: str


@dataclass(frozen=True)
class ContractConstructionResult:
    construction_id: str
    status: str
    strategy_id: Optional[str]
    capital_intent: str
    contracts: Tuple[NiftyOptionContract, ...]
    failure_reason: Optional[str]
    construction_trace: str
    spot_used: Optional[float]
    atm_strike_used: Optional[int]
    timestamp: str
    version: str
