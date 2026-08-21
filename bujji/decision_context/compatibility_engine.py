"""StrategyCompatibilityEngine -- Phase 19.4.

Classifies environmental compatibility only. Every rule below is
traceable to a real brain's OWN already-documented meaning -- never a
newly invented trading edge:

- VolatilityBrain's own module docstring: "Richness = IV / realized vol
  ... how much more [is priced in] is exactly what determines whether
  selling premium has a statistical edge today." IV_RICH -> premium
  selling compatible; IV_CHEAP -> premium buying compatible. This
  package did not invent that meaning -- it already existed in
  `bujji/intelligence/volatility_brain.py`'s own docstring before this
  phase existed.
- RegimeBrain's own COMPRESSED/VOLATILE classification, used verbatim
  for the compression/expansion-shaped families.
- LiquidityBrain's own TIGHT/NORMAL/WIDE tightness, used as an entry/exit
  feasibility gate (a WIDE market blocks any family, regardless of
  otherwise-favorable volatility).

Families with NO real supporting signal anywhere in the six in-scope
brains (direction, term structure, futures positioning, underlying
holdings) are always reported UNASSESSED, never forced.
"""
from __future__ import annotations

from bujji.intelligence.market_intelligence_snapshot.models import MarketIntelligenceSnapshot
from bujji.intelligence.models import Richness, RegimeType, SpreadTightness
from bujji.msi_strategy_selection_foundation import taxonomy as ssf_taxonomy

from .models import StrategyCompatibilityAssessment

# Families this engine has ANY real basis to assess, grouped by which
# signal drives them. Every other family in ssf_taxonomy.ALL_STRATEGY_FAMILIES
# is always UNASSESSED (no direction, term-structure, or holdings data
# exists anywhere in a MarketIntelligenceSnapshot).
_PREMIUM_SELLING_FAMILIES = (
    ssf_taxonomy.NEUTRAL_PREMIUM_SELLING, ssf_taxonomy.IRON_CONDOR, ssf_taxonomy.IRON_FLY,
)
_PREMIUM_BUYING_FAMILIES = (
    ssf_taxonomy.NEUTRAL_PREMIUM_BUYING, ssf_taxonomy.VOLATILITY_EXPANSION,
)
_COMPRESSION_FAMILIES = (ssf_taxonomy.VOLATILITY_COMPRESSION,)

_ASSESSABLE_FAMILIES = frozenset(_PREMIUM_SELLING_FAMILIES + _PREMIUM_BUYING_FAMILIES + _COMPRESSION_FAMILIES)


def assess_strategy_compatibility(snapshot: MarketIntelligenceSnapshot) -> StrategyCompatibilityAssessment:
    volatility = snapshot.volatility
    liquidity = snapshot.liquidity
    regime = snapshot.regime

    compatible = []
    incompatible = []
    supporting = []
    blocking = []

    liquidity_blocks_entry = liquidity.tightness == SpreadTightness.WIDE
    if liquidity_blocks_entry:
        blocking.append(f"liquidity: {liquidity.tightness.value} spread -- entry/exit feasibility blocked ({liquidity.reason})")

    # --- Premium selling shaped families: driven by VolatilityBrain's own richness meaning ---
    if volatility.richness == Richness.IV_RICH and not liquidity_blocks_entry:
        compatible.extend(_PREMIUM_SELLING_FAMILIES)
        supporting.append(f"volatility: IV_RICH ({volatility.reason}) -- premium selling has a statistical edge per VolatilityBrain's own documented meaning")
    elif volatility.richness == Richness.IV_CHEAP or liquidity_blocks_entry:
        incompatible.extend(_PREMIUM_SELLING_FAMILIES)
        if volatility.richness == Richness.IV_CHEAP:
            blocking.append(f"volatility: IV_CHEAP ({volatility.reason}) -- no statistical edge for premium selling")
    else:
        pass  # richness UNKNOWN/IV_FAIR -- stays unassessed for this family group

    # --- Premium buying shaped families: inverse of the above ---
    if volatility.richness == Richness.IV_CHEAP and not liquidity_blocks_entry:
        compatible.extend(_PREMIUM_BUYING_FAMILIES)
        supporting.append(f"volatility: IV_CHEAP ({volatility.reason}) -- premium buying favorable per VolatilityBrain's own documented meaning")
    elif volatility.richness == Richness.IV_RICH or liquidity_blocks_entry:
        incompatible.extend(_PREMIUM_BUYING_FAMILIES)
        if volatility.richness == Richness.IV_RICH:
            blocking.append(f"volatility: IV_RICH ({volatility.reason}) -- buying premium at a statistical disadvantage")

    # --- Volatility-compression-shaped family: driven directly by RegimeBrain's own classification ---
    if regime.regime == RegimeType.COMPRESSED:
        compatible.extend(_COMPRESSION_FAMILIES)
        supporting.append(f"regime: COMPRESSED ({regime.reason})")
    elif regime.regime == RegimeType.VOLATILE:
        incompatible.extend(_COMPRESSION_FAMILIES)
        blocking.append(f"regime: VOLATILE ({regime.reason}) -- contradicts a compression-shaped family")

    assessed = set(compatible) | set(incompatible)
    unassessed = tuple(f for f in ssf_taxonomy.ALL_STRATEGY_FAMILIES if f not in assessed)

    return StrategyCompatibilityAssessment(
        compatible_strategy_families=tuple(sorted(set(compatible))),
        incompatible_strategy_families=tuple(sorted(set(incompatible))),
        unassessed_strategy_families=unassessed,
        supporting_evidence=tuple(supporting),
        blocking_evidence=tuple(blocking),
    )
