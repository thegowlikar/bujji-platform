"""Multi-Domain Consensus Intelligence models — frozen, immutable
records.

Implements Deliverable 5's `Contradiction`, Deliverable 6-equivalent
`Explanation` (this sprint's 5 mandatory questions), and Deliverable 2's
`ConsensusAssessment`. Every dataclass here is `frozen=True` and
carries no logic — construction lives in `engine.py`, mirroring
`bujji.msi_price_structure.models`/`bujji.msi_market_structure.models`/
`bujji.msi_decision_synthesis.models` exactly.

---------------------------------------------------------------------
Design decision — assessment_id: deterministic content hash, over WHAT.
---------------------------------------------------------------------
`assessment_id` is a `hashlib.md5` hash over:
  * the sorted tuple of participating domain views' own content
    (`domain_name`, `lean`, `confidence`, `evidence_ids`,
    `source_assessment_id`) -- sorted by `domain_name` so supplying the
    same views in a different order still produces the same id
    (mirrors Series 77's `DomainSignal`-sorting precedent exactly);
  * the resulting `consensus_level`, `evidence_sufficiency`,
    `confidence_calibration` values (per this sprint's explicit
    instruction: "content-hash over participating domain assessment
    ids + resulting consensus/sufficiency values + schema_version");
  * `schema_version`.
NEVER over `timestamp`, NEVER `uuid4()`. Two identical sets of domain
views fed through `engine.compute_consensus()` twice, at two different
wall-clock times, always produce the identical `assessment_id` --
proven by `tests/test_msi_consensus_intelligence.py::
test_assessment_id_deterministic_same_input_same_id`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple


# ---------------------------------------------------------------------------
# Contradiction — Deliverable 4/5. A cross-DOMAIN disagreement record
# (dimension_a/dimension_b here are DOMAIN NAMES, not sub-fields of one
# brain's own assessment, since MDCI measures agreement ACROSS brains,
# not within one). Never resolved -- only surfaced (see engine.py's
# `detect_cross_domain_contradictions` docstring).
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Contradiction:
    dimension_a: str        # The first conflicting domain's name (e.g. "PRICE_STRUCTURE").
    value_a: str             # That domain's lean (one of taxonomy.CONSIDERED_LEANS).
    dimension_b: str        # The second conflicting domain's name.
    value_b: str             # That domain's lean.
    reason: str              # Deterministic, human-readable explanation of why these two reads conflict.


# ---------------------------------------------------------------------------
# Explanation — mandatory. Answers this sprint's 5 required questions
# as real computed content, never templated prose with no substance.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Explanation:
    assessment_id: str
    which_domains_agree: Tuple[str, ...]                  # = agreeing_domains, first-class here too (never re-derived by a caller).
    which_domains_disagree: Tuple[str, ...]               # = conflicting_domains, first-class.
    which_evidence_is_missing: Tuple[str, ...]             # missing_domains, plus any participating domain that cited zero evidence_ids, stated explicitly.
    why_consensus_is_high_or_low: str                     # Real computed statement citing the actual agreement_ratio and considered/agreeing/conflicting counts.
    what_additional_domains_would_increase_confidence: Tuple[str, ...]   # Deterministic, mechanical statements only -- never speculative.
    what_changed: Optional[str]                            # Vs. the previous assessment (by assessment_id); None if there was no previous assessment.
    schema_version: str


# ---------------------------------------------------------------------------
# ConsensusAssessment — Deliverable 2, immutable.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ConsensusAssessment:
    assessment_id: str
    timestamp: str
    participating_domains: Tuple[str, ...]        # Every domain that supplied a view this cycle (considered + ambiguous).
    agreeing_domains: Tuple[str, ...]              # Considered domains whose lean equals the winning lean.
    conflicting_domains: Tuple[str, ...]           # Considered domains whose lean differs from the winning lean -- NEVER silently emptied while a genuine conflict exists.
    missing_domains: Tuple[str, ...]               # Expected domains (per the expected-domain registry) that did NOT participate this cycle.
    consensus_level: str                           # One of taxonomy.ALL_CONSENSUS_LEVELS.
    evidence_sufficiency: str                      # One of taxonomy.ALL_EVIDENCE_SUFFICIENCY_LEVELS -- an INDEPENDENT dimension from consensus_level (see taxonomy.py).
    contradiction_density: float                   # conflicting_domain_count / considered_domain_count (0.0 if considered_domain_count == 0). See config.py's disclosed formula.
    confidence_calibration: str                    # One of taxonomy.ALL_CONFIDENCE_CALIBRATIONS.
    supporting_assessment_ids: Tuple[str, ...]      # The INPUT brains' own assessment_ids (referenced, never copied) -- only non-None source_assessment_ids, sorted.
    explanation: Explanation
    provenance: str                                 # Free-text description of what produced this assessment.
    schema_version: str
