"""Multi-Domain Consensus Intelligence vocabulary — BUJJI Engineering
Series 81 (MDCI v1).

Following this project's established convention (see
`bujji/msi_decision_synthesis/taxonomy.py`, `bujji/msi_market_structure/
taxonomy.py`), closed vocabularies here are plain string constants
collected into `ALL_*` tuples, not `enum.Enum` classes.

---------------------------------------------------------------------
Design note — the "lean-mapping" concept, MDCI's own responsibility:
---------------------------------------------------------------------
Series 78 (Price Structure) and Series 79 (Market Structure) publish
assessments in fundamentally different vocabularies — `trend_state`
vs. `support_state`, `compression_state` vs. `breakout_state`, etc.
Two different brains' assessments cannot be compared for "agreement"
by naive string equality; there is no shared field to compare at all.

Series 77 (Decision Synthesis) faced an analogous-looking problem and
solved it with `config.STATE_LEAN_MAP`, reducing each domain's own
free-text `state` string to one of three OPPORTUNITY-flavored leans
(`OPPORTUNITY_FORMING` / `NEUTRAL_FORMING` / `AMBIGUOUS`) before voting.
That concept — "reduce heterogeneous domain output to a small, shared,
coarser lean before comparing across domains" — is directly reusable
HERE, as a CONCEPT. It is NOT reusable as code: Series 77's lean
vocabulary is specifically about opportunity-formation (is a tradeable
setup emerging), which is a different question from what MDCI asks
(do the brains' reads, whatever they mean substantively, POINT the
same direction or not). Importing `bujji.msi_decision_synthesis` would
also violate this package's own domain-agnostic isolation mandate (see
`__init__.py`). MDCI therefore defines its OWN, smaller, purpose-built
lean vocabulary below (`CONSENSUS_LEAN_*`), and the reduction from a
real brain's assessment into one of these leans is done entirely by
the CALLER (never inside `bujji.msi_consensus` — see `engine.py`'s
`DomainAssessmentView`, which already expects a pre-computed lean, not
a raw brain assessment).

Three considered leans (`BULLISH_LEANING`, `BEARISH_LEANING`,
`NEUTRAL_LEANING`) participate in the agreement/conflict vote.
A fourth, `AMBIGUOUS_LEANING`, exists for domain views whose
translation cannot be meaningfully reduced to a directional read at
all (mirrors Series 77's `PRECONDITION_DOMAINS`/`AMBIGUOUS` exclusion
concept) — such views never vote, and are neither "agreeing" nor
"conflicting," but they still count toward evidence/coverage
accounting.
---------------------------------------------------------------------
"""
from __future__ import annotations

MSI_CONSENSUS_VERSION = "1.0.0"

RECOGNIZED_SCHEMA_VERSIONS = ("1.0.0",)

# ---------------------------------------------------------------------------
# Lean vocabulary — MDCI's own, purpose-built, small, coarse
# reduction target. See module docstring.
# ---------------------------------------------------------------------------
LEAN_BULLISH = "BULLISH_LEANING"
LEAN_BEARISH = "BEARISH_LEANING"
LEAN_NEUTRAL = "NEUTRAL_LEANING"
LEAN_AMBIGUOUS = "AMBIGUOUS_LEANING"

CONSIDERED_LEANS = (LEAN_BULLISH, LEAN_BEARISH, LEAN_NEUTRAL)
ALL_LEANS = CONSIDERED_LEANS + (LEAN_AMBIGUOUS,)

# ---------------------------------------------------------------------------
# ConsensusLevel — Deliverable 3. Independent dimension from
# EvidenceSufficiency (see below): this measures AGREEMENT RATIO only.
# ---------------------------------------------------------------------------
CONSENSUS_NO_CONSENSUS = "NO_CONSENSUS"
CONSENSUS_WEAK = "WEAK_CONSENSUS"
CONSENSUS_MODERATE = "MODERATE_CONSENSUS"
CONSENSUS_STRONG = "STRONG_CONSENSUS"
CONSENSUS_UNANIMOUS = "UNANIMOUS_CONSENSUS"

ALL_CONSENSUS_LEVELS = (
    CONSENSUS_NO_CONSENSUS,
    CONSENSUS_WEAK,
    CONSENSUS_MODERATE,
    CONSENSUS_STRONG,
    CONSENSUS_UNANIMOUS,
)

CONSENSUS_LEVEL_RANK = {
    CONSENSUS_NO_CONSENSUS: 0,
    CONSENSUS_WEAK: 1,
    CONSENSUS_MODERATE: 2,
    CONSENSUS_STRONG: 3,
    CONSENSUS_UNANIMOUS: 4,
}

# ---------------------------------------------------------------------------
# EvidenceSufficiency — Deliverable 3, EXPLICITLY a separate dimension
# from ConsensusLevel: two brains can agree completely (UNANIMOUS
# consensus) while the collection is still evidentially thin (only 2
# of 9 expected domains participated, each citing a single evidence
# id) -- INSUFFICIENT/LIMITED evidence sufficiency despite unanimous
# agreement. Conversely, many domains can participate with dense
# evidence yet genuinely disagree (ROBUST sufficiency, NO_CONSENSUS
# level). Neither formula reads the other's inputs (see engine.py).
# ---------------------------------------------------------------------------
SUFFICIENCY_INSUFFICIENT = "INSUFFICIENT"
SUFFICIENCY_LIMITED = "LIMITED"
SUFFICIENCY_ADEQUATE = "ADEQUATE"
SUFFICIENCY_ROBUST = "ROBUST"

ALL_EVIDENCE_SUFFICIENCY_LEVELS = (
    SUFFICIENCY_INSUFFICIENT,
    SUFFICIENCY_LIMITED,
    SUFFICIENCY_ADEQUATE,
    SUFFICIENCY_ROBUST,
)

SUFFICIENCY_RANK = {
    SUFFICIENCY_INSUFFICIENT: 0,
    SUFFICIENCY_LIMITED: 1,
    SUFFICIENCY_ADEQUATE: 2,
    SUFFICIENCY_ROBUST: 3,
}

# ---------------------------------------------------------------------------
# ConfidenceCalibration — Deliverable 2's field. Measures whether a
# domain's OWN self-reported confidence is internally consistent with
# the evidence it cites -- e.g. a domain claiming high confidence off
# a single, thin evidence trail (OVERCONFIDENT) vs. a domain claiming
# low confidence despite dense, well-corroborated evidence
# (UNDERCONFIDENT). UNKNOWN is used whenever there isn't enough basis
# to judge calibration at all (e.g. zero evidence ids cited, or zero
# participating domains) -- never fabricated as WELL_CALIBRATED by
# default.
# ---------------------------------------------------------------------------
CALIBRATION_WELL_CALIBRATED = "WELL_CALIBRATED"
CALIBRATION_OVERCONFIDENT = "OVERCONFIDENT"
CALIBRATION_UNDERCONFIDENT = "UNDERCONFIDENT"
CALIBRATION_UNKNOWN = "UNKNOWN"

ALL_CONFIDENCE_CALIBRATIONS = (
    CALIBRATION_WELL_CALIBRATED,
    CALIBRATION_OVERCONFIDENT,
    CALIBRATION_UNDERCONFIDENT,
    CALIBRATION_UNKNOWN,
)

# ---------------------------------------------------------------------------
# Expected-domain registry (Deliverable 4's "incomplete domain
# coverage" check). This is a DISCLOSED, fixed vocabulary of domain
# names MDCI expects might participate in a given cycle -- reused,
# by NAME ONLY (plain strings, no import), from
# `bujji.msi_decision_synthesis.taxonomy.ALL_MSI_DOMAINS`'s same 9
# names, since that is this project's one existing disclosed MSI
# domain-name vocabulary and re-inventing a second one would create a
# genuine naming-drift risk. Per this package's isolation mandate
# (see __init__.py), these are re-declared here as plain strings, NOT
# imported from `bujji.msi_decision_synthesis`.
# ---------------------------------------------------------------------------
DOMAIN_PRICE_STRUCTURE = "PRICE_STRUCTURE"
DOMAIN_SUPPORT_RESISTANCE = "SUPPORT_RESISTANCE"
DOMAIN_OPTIONS_MARKET_STRUCTURE = "OPTIONS_MARKET_STRUCTURE"
DOMAIN_FUTURES_STRUCTURE = "FUTURES_STRUCTURE"
DOMAIN_VOLATILITY_STRUCTURE = "VOLATILITY_STRUCTURE"
DOMAIN_LIQUIDITY = "LIQUIDITY"
DOMAIN_TIME_STRUCTURE = "TIME_STRUCTURE"
DOMAIN_CROSS_ASSET = "CROSS_ASSET"
DOMAIN_REGIME_INTELLIGENCE = "REGIME_INTELLIGENCE"

ALL_MSI_DOMAINS = (
    DOMAIN_PRICE_STRUCTURE,
    DOMAIN_SUPPORT_RESISTANCE,
    DOMAIN_OPTIONS_MARKET_STRUCTURE,
    DOMAIN_FUTURES_STRUCTURE,
    DOMAIN_VOLATILITY_STRUCTURE,
    DOMAIN_LIQUIDITY,
    DOMAIN_TIME_STRUCTURE,
    DOMAIN_CROSS_ASSET,
    DOMAIN_REGIME_INTELLIGENCE,
)
