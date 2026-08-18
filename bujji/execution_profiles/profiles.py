"""Execution Reality Profiles -- Phase 20.2.

Every number in this file is MODELED, CALIBRATION PENDING -- a
documented assumption, not a measured market fact. Phase 20.0 already
established that no historical bid/ask/spread/depth data exists for
NIFTY futures or options in this system, so nothing here can be
calibrated against real captured spread/impact data yet. Every profile
is therefore Level C (deterministic simulation from documented
assumptions) -- never Level A or B -- per
docs/PHASE_20_2_EXECUTION_REALITY_REPORT.md.

Slippage is modeled as PERCENTAGE-of-price rather than FIXED_TICK,
deliberately -- this codebase has not independently verified NIFTY
futures' exact exchange tick size, and asserting one here as fact
would be exactly the kind of unverified-but-confident number this
engagement's anti-fabrication discipline forbids. A percentage-of-
price model needs no tick-size assumption at all.

Charges are identical across all three profiles: brokerage/STT/GST/
exchange charges/SEBI charges/stamp duty do not change with market
stress, only fill quality does -- so every profile reuses
`ChargesConfig()`'s own existing illustrative defaults (bujji/broker/
simulation/charges.py), unmodified, rather than inventing a second
set of charge assumptions.

Rejection under STRESS/EXTREME is liquidity-score-gated, never
probabilistic -- `FillSimulator` accepts no randomness for rejection,
and this module introduces none. If a caller never supplies
`MarketSnapshot.liquidity_score`, STRESS/EXTREME profiles reject
nothing (a missing signal is never treated as "definitely illiquid"),
exactly mirroring `MarketSnapshot`'s own "missing means unknown"
contract.
"""
from __future__ import annotations

from dataclasses import dataclass

from bujji.broker.simulation.charges import ChargesConfig
from bujji.broker.simulation.fill_simulator import LatencyConfig, LatencyMode, RejectionConfig
from bujji.broker.simulation.slippage import SlippageConfig, SlippageMode

LEVEL_C = "C"
CALIBRATION_PENDING = "CALIBRATION_PENDING"


@dataclass(frozen=True)
class ExecutionProfile:
    name: str
    level: str
    calibration_status: str
    slippage: SlippageConfig
    latency: LatencyConfig
    rejection: RejectionConfig
    charges: ChargesConfig


# MODELED: ~0.02% adverse price impact -- a light, liquid-market assumption.
NORMAL = ExecutionProfile(
    name="NORMAL", level=LEVEL_C, calibration_status=CALIBRATION_PENDING,
    slippage=SlippageConfig(mode=SlippageMode.PERCENTAGE, percentage=0.0002),
    latency=LatencyConfig(mode=LatencyMode.FIXED, fixed_ms=150.0),
    rejection=RejectionConfig(),
    charges=ChargesConfig(),
)

# MODELED: ~0.05% adverse price impact, higher latency, rejects only if
# a caller-supplied liquidity_score falls below 0.3 (never fabricated).
STRESS = ExecutionProfile(
    name="STRESS", level=LEVEL_C, calibration_status=CALIBRATION_PENDING,
    slippage=SlippageConfig(mode=SlippageMode.PERCENTAGE, percentage=0.0005),
    latency=LatencyConfig(mode=LatencyMode.FIXED, fixed_ms=400.0),
    rejection=RejectionConfig(reject_on_insufficient_liquidity=True, min_liquidity_score=0.3),
    charges=ChargesConfig(),
)

# MODELED: ~0.15% adverse price impact, highest latency, rejects if a
# caller-supplied liquidity_score falls below 0.6 (never fabricated).
EXTREME = ExecutionProfile(
    name="EXTREME", level=LEVEL_C, calibration_status=CALIBRATION_PENDING,
    slippage=SlippageConfig(mode=SlippageMode.PERCENTAGE, percentage=0.0015),
    latency=LatencyConfig(mode=LatencyMode.FIXED, fixed_ms=1000.0),
    rejection=RejectionConfig(reject_on_insufficient_liquidity=True, min_liquidity_score=0.6),
    charges=ChargesConfig(),
)

_ALL = {"NORMAL": NORMAL, "STRESS": STRESS, "EXTREME": EXTREME}


class UnknownExecutionProfileError(Exception):
    """Raised on an unrecognized profile name -- never silently defaulted."""


def get_profile(name: str) -> ExecutionProfile:
    try:
        return _ALL[name]
    except KeyError:
        raise UnknownExecutionProfileError(
            f"unrecognized execution profile {name!r}, expected one of {sorted(_ALL)}"
        ) from None
