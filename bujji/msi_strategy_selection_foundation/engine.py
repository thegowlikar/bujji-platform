"""Strategy Selection Foundation engine — pure functions, no state, no
IO, no wall-clock reads.

---------------------------------------------------------------------
Design note — this is a DECLARATIVE gate, not a scorer (Deliverable 5,
Deliverable 6's own constraint list: "No scoring. No ranking. No
optimisation. No choosing."):
---------------------------------------------------------------------
`assess_strategy_suitability` reads one family's structural definition
(taxonomy.STRATEGY_DEFINITIONS) and checks it against real, available
evidence (MDI's overall_direction, Consensus's consensus_level, MSSI's
structure_location, and now — Series 88 follow-up — VSB's
volatility_regime/iv_state/expansion_state/compression_state). Every
family is assessed INDEPENDENTLY -- there is no comparison, no "best"
output, no numeric score anywhere in this module. `assess_all_families`
simply maps this same function over every family and returns the
results as an unordered tuple.

This module imports `bujji.msi_market_direction.models.MarketDirectionAssessment`,
`bujji.msi_market_structure.models.MarketStructureAssessment`,
`bujji.msi_consensus.models.ConsensusAssessment`, and (new)
`bujji.msi_volatility_structure.models.VolatilityStructureAssessment`
DIRECTLY -- a deliberate downstream-consumption exception (this
package sits strictly downstream of all four), mirroring Series
82/85/88's own established precedent for the same kind of dependency.

---------------------------------------------------------------------
Volatility gating design (Series 88 follow-up — "wire SSF to consume
Volatility Structure evidence"), one rule per family that requires
DOMAIN_VOLATILITY, each with disclosed reasoning:
---------------------------------------------------------------------
- NEUTRAL_PREMIUM_SELLING: iv_state in (IV_RICH, IV_FAIR) AND
  expansion_state != CONFIRMED. Selling premium wants IV rich enough to
  be worth collecting, and NOT actively expanding (selling into rising
  vol is the textbook way premium selling blows up).
- NEUTRAL_PREMIUM_BUYING: iv_state == IV_CHEAP OR compression_state ==
  CONFIRMED. Buying premium wants it cheap, or wants to be positioned
  ahead of an anticipated expansion out of compression.
- VOLATILITY_EXPANSION: compression_state == CONFIRMED OR
  volatility_regime == REGIME_COMPRESSED. The objective is to profit
  FROM expansion, so the entry condition is CURRENTLY compressed (the
  anticipated move), not already expanding (that edge would already be
  gone).
- VOLATILITY_COMPRESSION: expansion_state == CONFIRMED OR
  volatility_regime == REGIME_HIGH_VOLATILITY. Mirror of the above —
  profiting from vol falling means entering while it's currently
  elevated/expanding.
- RATIO: iv_state != IV_UNKNOWN. Ratio spreads work across a range of
  vol levels; the requirement is simply that SOME real volatility read
  exists, not a specific level.
- BUTTERFLY: iv_state == IV_RICH OR compression_state == CONFIRMED.
  A precision pin play is cost-efficient when premium is rich (cheaper
  breakevens relative to width) or when compression suggests price is
  likely to stay pinned.

IRON_CONDOR/IRON_FLY/CALENDAR are NOT in this rule set: the former two
remain gated on DOMAIN_LIQUIDITY (still unavailable) regardless of
volatility; CALENDAR requires DOMAIN_VOLATILITY_TERM_STRUCTURE
specifically (still unavailable, deliberately kept distinct from plain
DOMAIN_VOLATILITY — see taxonomy.py's module docstring).
"""
from __future__ import annotations

import hashlib
from typing import Callable, Dict, List, Optional, Tuple

from bujji.msi_consensus.models import ConsensusAssessment
from bujji.msi_consensus import taxonomy as consensus_taxonomy
from bujji.msi_market_direction.models import MarketDirectionAssessment
from bujji.msi_market_structure.models import MarketStructureAssessment
from bujji.msi_market_structure import taxonomy as mssi_taxonomy
from bujji.msi_volatility_structure.models import VolatilityStructureAssessment
from bujji.msi_volatility_structure import taxonomy as vsb_taxonomy

from . import config as _config
from . import taxonomy
from .models import Explanation, StrategySuitabilityAssessment


def _missing_domains_for(family: str) -> Tuple[str, ...]:
    definition = taxonomy.STRATEGY_DEFINITIONS[family]
    required = definition["required_evidence"]
    return tuple(d for d in required if d in taxonomy.UNAVAILABLE_DOMAINS)


def _rule_neutral_premium_selling(vsb: VolatilityStructureAssessment) -> Tuple[bool, str]:
    ok = vsb.iv_state in (vsb_taxonomy.IV_RICH, vsb_taxonomy.IV_FAIR) and vsb.expansion_state != vsb_taxonomy.EXPANSION_CONFIRMED
    return ok, (
        f"iv_state={vsb.iv_state}/expansion_state={vsb.expansion_state}: "
        f"{'rich-enough and not actively expanding -- supports selling premium' if ok else 'does not support selling premium safely (either too cheap or actively expanding)'}."
    )


def _rule_neutral_premium_buying(vsb: VolatilityStructureAssessment) -> Tuple[bool, str]:
    ok = vsb.iv_state == vsb_taxonomy.IV_CHEAP or vsb.compression_state == vsb_taxonomy.COMPRESSION_CONFIRMED
    return ok, (
        f"iv_state={vsb.iv_state}/compression_state={vsb.compression_state}: "
        f"{'cheap premium or compressed ahead of an anticipated expansion -- supports buying premium' if ok else 'premium is not cheap and no compression detected -- does not support buying premium'}."
    )


def _rule_volatility_expansion(vsb: VolatilityStructureAssessment) -> Tuple[bool, str]:
    ok = vsb.compression_state == vsb_taxonomy.COMPRESSION_CONFIRMED or vsb.volatility_regime == vsb_taxonomy.REGIME_COMPRESSED
    return ok, (
        f"compression_state={vsb.compression_state}/volatility_regime={vsb.volatility_regime}: "
        f"{'currently compressed -- consistent with an expansion objective' if ok else 'not currently compressed -- the expansion edge (if any) may already be spent'}."
    )


def _rule_volatility_compression(vsb: VolatilityStructureAssessment) -> Tuple[bool, str]:
    ok = vsb.expansion_state == vsb_taxonomy.EXPANSION_CONFIRMED or vsb.volatility_regime == vsb_taxonomy.REGIME_HIGH_VOLATILITY
    return ok, (
        f"expansion_state={vsb.expansion_state}/volatility_regime={vsb.volatility_regime}: "
        f"{'currently elevated/expanding -- consistent with a compression objective' if ok else 'not currently elevated -- the compression edge (if any) may already be spent'}."
    )


def _rule_ratio(vsb: VolatilityStructureAssessment) -> Tuple[bool, str]:
    ok = vsb.iv_state != vsb_taxonomy.IV_UNKNOWN
    return ok, f"iv_state={vsb.iv_state}: {'a real volatility read exists' if ok else 'no real volatility read exists'}."


def _rule_butterfly(vsb: VolatilityStructureAssessment) -> Tuple[bool, str]:
    ok = vsb.iv_state == vsb_taxonomy.IV_RICH or vsb.compression_state == vsb_taxonomy.COMPRESSION_CONFIRMED
    return ok, (
        f"iv_state={vsb.iv_state}/compression_state={vsb.compression_state}: "
        f"{'rich premium or compression supports a cost-efficient pin play' if ok else 'neither rich premium nor compression detected'}."
    )


_VOLATILITY_RULES: Dict[str, Callable[[VolatilityStructureAssessment], Tuple[bool, str]]] = {
    taxonomy.NEUTRAL_PREMIUM_SELLING: _rule_neutral_premium_selling,
    taxonomy.NEUTRAL_PREMIUM_BUYING: _rule_neutral_premium_buying,
    taxonomy.VOLATILITY_EXPANSION: _rule_volatility_expansion,
    taxonomy.VOLATILITY_COMPRESSION: _rule_volatility_compression,
    taxonomy.RATIO: _rule_ratio,
    taxonomy.BUTTERFLY: _rule_butterfly,
}


def assess_strategy_suitability(
    family: str,
    mdi: MarketDirectionAssessment,
    mssi: MarketStructureAssessment,
    consensus: ConsensusAssessment,
    vsb: Optional[VolatilityStructureAssessment] = None,
    *,
    timestamp: str,
    provenance: str = _config.DEFAULT_PROVENANCE,
    schema_version: str = _config.SCHEMA_VERSION,
) -> StrategySuitabilityAssessment:
    definition = taxonomy.STRATEGY_DEFINITIONS[family]
    missing_domains = list(_missing_domains_for(family))

    # A family requiring DOMAIN_VOLATILITY but given no real `vsb`
    # instance for THIS call is a genuine per-call evidence gap,
    # distinct from "the domain doesn't exist anywhere in this
    # codebase" (which _missing_domains_for already covers via
    # UNAVAILABLE_DOMAINS). Both cases honestly resolve to
    # INSUFFICIENT_EVIDENCE.
    if taxonomy.DOMAIN_VOLATILITY in definition["required_evidence"] and vsb is None:
        missing_domains.append(taxonomy.DOMAIN_VOLATILITY)
    missing_domains = tuple(sorted(set(missing_domains)))

    supporting_reasons: List[str] = []
    rejecting_reasons: List[str] = []
    supporting_evidence: List[str] = []
    rejecting_evidence: List[str] = []

    if missing_domains:
        suitability = taxonomy.INSUFFICIENT_EVIDENCE
        confidence = taxonomy.CONFIDENCE_NONE
        rejecting_reasons.append(
            f"Required evidence domain(s) {missing_domains} are genuinely unavailable "
            f"({'for this specific call' if taxonomy.DOMAIN_VOLATILITY in missing_domains and vsb is None and taxonomy.DOMAIN_VOLATILITY not in taxonomy.UNAVAILABLE_DOMAINS else 'in this codebase today'}) "
            f"-- suitability cannot be determined, honestly reported as insufficient rather than guessed."
        )
    else:
        direction_ok = True
        if definition["required_direction_leans"]:
            direction_ok = mdi.overall_direction in definition["required_direction_leans"]
            if direction_ok:
                supporting_reasons.append(
                    f"overall_direction={mdi.overall_direction} is within the required set "
                    f"{definition['required_direction_leans']} for {family}."
                )
                supporting_evidence.append(mdi.assessment_id)
            else:
                rejecting_reasons.append(
                    f"overall_direction={mdi.overall_direction} is not within the required set "
                    f"{definition['required_direction_leans']} for {family}."
                )
                rejecting_evidence.append(mdi.assessment_id)

        direction_forbidden = False
        if definition["forbidden_direction_leans"] and mdi.overall_direction in definition["forbidden_direction_leans"]:
            direction_forbidden = True
            rejecting_reasons.append(
                f"overall_direction={mdi.overall_direction} is explicitly forbidden for {family}."
            )
            rejecting_evidence.append(mdi.assessment_id)

        consensus_rank = consensus_taxonomy.CONSENSUS_LEVEL_RANK[consensus.consensus_level]
        consensus_ok = consensus_rank >= definition["required_min_consensus_rank"]
        if consensus_ok:
            supporting_reasons.append(
                f"consensus_level={consensus.consensus_level} (rank {consensus_rank}) meets the "
                f"minimum required rank {definition['required_min_consensus_rank']} for {family}."
            )
            supporting_evidence.append(consensus.assessment_id)
        else:
            rejecting_reasons.append(
                f"consensus_level={consensus.consensus_level} (rank {consensus_rank}) is below the "
                f"minimum required rank {definition['required_min_consensus_rank']} for {family}."
            )
            rejecting_evidence.append(consensus.assessment_id)

        structure_ok = True
        if taxonomy.DOMAIN_MARKET_STRUCTURE in definition["required_evidence"]:
            structure_ok = mssi.structure_location in (
                mssi_taxonomy.LOCATION_INSIDE_RANGE, mssi_taxonomy.LOCATION_NEAR_SUPPORT, mssi_taxonomy.LOCATION_NEAR_RESISTANCE,
            )
            if structure_ok:
                supporting_reasons.append(
                    f"structure_location={mssi.structure_location} is consistent with a bounded/pinned "
                    f"structural read required by {family}."
                )
                supporting_evidence.append(mssi.assessment_id)
            else:
                rejecting_reasons.append(
                    f"structure_location={mssi.structure_location} does not support the bounded/pinned "
                    f"structural read required by {family}."
                )
                rejecting_evidence.append(mssi.assessment_id)

        volatility_ok = True
        if taxonomy.DOMAIN_VOLATILITY in definition["required_evidence"] and vsb is not None:
            rule = _VOLATILITY_RULES.get(family)
            if rule is not None:
                volatility_ok, reason = rule(vsb)
                if volatility_ok:
                    supporting_reasons.append(f"{reason} (volatility condition for {family}: satisfied)")
                    supporting_evidence.append(vsb.assessment_id)
                else:
                    rejecting_reasons.append(f"{reason} (volatility condition for {family}: not satisfied)")
                    rejecting_evidence.append(vsb.assessment_id)

        if direction_forbidden or not direction_ok or not consensus_ok or not structure_ok or not volatility_ok:
            suitability = taxonomy.UNSUITABLE
        else:
            suitability = taxonomy.SUITABLE

        base_conf_rank = taxonomy.confidence_rank(definition["required_confidence"])
        if consensus_rank >= _config.CONSENSUS_RANK_FOR_HIGH_CONFIDENCE:
            confidence = taxonomy.confidence_at_rank(min(base_conf_rank + 1, taxonomy.confidence_rank(taxonomy.CONFIDENCE_HIGH)))
        elif consensus_rank >= _config.CONSENSUS_RANK_FOR_MODERATE_CONFIDENCE:
            confidence = definition["required_confidence"]
        else:
            confidence = taxonomy.confidence_at_rank(max(base_conf_rank - 1, taxonomy.confidence_rank(taxonomy.CONFIDENCE_NONE)))
        if suitability == taxonomy.UNSUITABLE:
            confidence = taxonomy.confidence_at_rank(max(taxonomy.confidence_rank(confidence) - 1, 0))

    ids = {mdi.assessment_id, mssi.assessment_id, consensus.assessment_id}
    if vsb is not None:
        ids.add(vsb.assessment_id)
    supporting_assessment_ids = tuple(sorted(ids))

    assessment_id = _assessment_id(family, supporting_assessment_ids, suitability, confidence, missing_domains, schema_version)

    explanation = Explanation(
        assessment_id=assessment_id,
        why_suitable=tuple(supporting_reasons) if suitability == taxonomy.SUITABLE else (),
        why_unsuitable=tuple(rejecting_reasons) if suitability in (taxonomy.UNSUITABLE, taxonomy.INSUFFICIENT_EVIDENCE) else (),
        supporting_evidence=tuple(supporting_evidence),
        rejecting_evidence=tuple(rejecting_evidence),
        missing_evidence=missing_domains,
        schema_version=schema_version,
    )

    return StrategySuitabilityAssessment(
        assessment_id=assessment_id,
        timestamp=timestamp,
        strategy_family=family,
        suitability=suitability,
        supporting_reasons=tuple(supporting_reasons),
        rejecting_reasons=tuple(rejecting_reasons),
        required_missing_evidence=missing_domains,
        confidence=confidence,
        supporting_assessment_ids=supporting_assessment_ids,
        explanation=explanation,
        provenance=provenance,
        schema_version=schema_version,
    )


def assess_all_families(
    mdi: MarketDirectionAssessment,
    mssi: MarketStructureAssessment,
    consensus: ConsensusAssessment,
    vsb: Optional[VolatilityStructureAssessment] = None,
    *,
    timestamp: str,
    provenance: str = _config.DEFAULT_PROVENANCE,
    schema_version: str = _config.SCHEMA_VERSION,
) -> Tuple[StrategySuitabilityAssessment, ...]:
    """Deliverable 5 -- every family assessed independently; this
    function is a pure map, never a comparison/ranking. `vsb` is
    optional and backward-compatible: calling this with only
    (mdi, mssi, consensus), as every pre-Series-88-follow-up caller
    still does, produces IDENTICAL results to before this change for
    every family that does NOT require DOMAIN_VOLATILITY, and honestly
    INSUFFICIENT_EVIDENCE (not a crash, not a silent guess) for every
    family that does."""
    return tuple(
        assess_strategy_suitability(family, mdi, mssi, consensus, vsb, timestamp=timestamp, provenance=provenance, schema_version=schema_version)
        for family in taxonomy.ALL_STRATEGY_FAMILIES
    )


def _assessment_id(
    family: str,
    supporting_assessment_ids: Tuple[str, ...],
    suitability: str,
    confidence: str,
    missing_domains: Tuple[str, ...],
    schema_version: str,
) -> str:
    seed = "###".join([
        family, "|".join(sorted(supporting_assessment_ids)), suitability, confidence,
        "|".join(sorted(missing_domains)), schema_version,
    ])
    return "SSA-" + hashlib.md5(seed.encode()).hexdigest()[:24]
