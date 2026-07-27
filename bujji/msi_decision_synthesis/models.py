"""MSI Decision Synthesis Engine models — frozen, immutable records.

Implements Deliverable 1 (`DomainSignal`, the generic interim input
contract), Deliverable 2 (`MarketOpportunityAssessment`), and
Deliverable 6 (`Explanation`). Every dataclass here is `frozen=True`
and carries no logic -- construction lives in `engine.py`, never here.

---------------------------------------------------------------------
Design decision — assessment_id: deterministic content hash, over WHAT.
---------------------------------------------------------------------
`assessment_id` is a `hashlib.md5` hash over the sorted tuple of input
`DomainSignal` contents (domain_name, state, confidence, evidence_ids)
plus `schema_version` -- NEVER over `timestamp` and NEVER `uuid4()`.
Domain signals are sorted by `domain_name` before hashing so that
supplying the same signals in a different order still produces the
same `assessment_id` (order of arrival is not part of "the fact being
assessed"). Two identical sets of `DomainSignal`s fed through
`engine.synthesize()` twice, at two different wall-clock times, always
produce the identical `assessment_id` — proven by
`tests/test_msi_decision_synthesis_engine.py::test_synthesis_determinism`
and by the Deliverable 10 demonstration's "run twice, byte-identical"
requirement. This mirrors Series 76's `episode_id` design precisely:
hash only the immutable, meaning-bearing inputs, never wall-clock time.

---------------------------------------------------------------------
Design decision — episode_ids: plain strings, no import.
---------------------------------------------------------------------
`episode_ids` references Series 76 `Episode.episode_id` values by
plain string only. Even an ID-only reference could in principle carry
a type-level dependency (e.g. a `NewType`/typed wrapper imported from
`bujji.market_episode`), but that is deliberately rejected here: a
plain `Tuple[str, ...]` keeps this package's AST isolation boundary
exactly as simple as every prior series' (73B/73C/74/75/76) --
"reference by id, never import the producing package's code" — and
avoids DSE depending on Series 76's module even for a type. This
mirrors exactly how `Episode.originating_event_ids`/
`originating_observation_ids` reference Series 75/73A ids as plain
strings without importing those packages' model classes for the id
type itself.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple


# ---------------------------------------------------------------------------
# DomainSignal — Deliverable 1's generic, domain-neutral input contract.
# This is an INTERIM shape: no real MSI brain exists yet (Series 71
# Deliverable 8/9), so this is deliberately NOT modeled on any specific
# future brain's real output shape. Once real brains (Price Structure,
# Volatility Structure, ...) are built, each will need to either
# publish in this shape directly or be adapted to it by a thin
# translation layer outside this package -- DSE's `synthesize()` itself
# must never need to change to accommodate a new domain's *arrival*,
# only genuinely new domain *names* added to taxonomy.ALL_MSI_DOMAINS.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class DomainSignal:
    domain_name: str            # One of taxonomy.ALL_MSI_DOMAINS.
    state: str                  # The domain's own published classification string (e.g. "Trending", "Expanding") -- free text, domain-owned, never interpreted by DSE beyond taxonomy's disclosed lean mapping.
    confidence: float           # The domain's OWN self-reported confidence in its state, 0.0-1.0. Distinct from DSE's fused confidence_level.
    evidence_ids: Tuple[str, ...] = ()   # References only -- never copies -- the domain's Derived Evidence ids that produced `state`.


# ---------------------------------------------------------------------------
# MarketOpportunityAssessment — Deliverable 2, immutable.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class MarketOpportunityAssessment:
    assessment_id: str
    timestamp: str
    opportunity_state: str                       # One of taxonomy.ALL_OPPORTUNITY_STATES.
    confidence_level: str                        # One of taxonomy.ALL_CONFIDENCE_LEVELS.
    opportunity_quality: str                     # One of taxonomy.ALL_OPPORTUNITY_QUALITIES.
    supporting_domains: Tuple[str, ...]           # Domains whose lean agreed with the winning read.
    conflicting_domains: Tuple[str, ...]          # Domains that genuinely disagreed -- NEVER silently suppressed/emptied when disagreement exists (Deliverable 5).
    compatible_strategy_families: Tuple[str, ...]
    incompatible_strategy_families: Tuple[str, ...]
    evidence_ids: Tuple[str, ...]                 # Union of all contributing DomainSignals' evidence_ids -- references only.
    episode_ids: Tuple[str, ...]                  # Series 76 Episode ids this synthesis relates to -- plain strings, no import (see module docstring).
    provenance: str                               # Free-text description of what produced this assessment (e.g. "msi_decision_synthesis.engine.synthesize").
    schema_version: str


# ---------------------------------------------------------------------------
# Explanation — Deliverable 6, EXPLICITLY MANDATORY. Answers, for every
# assessment, the 7 required questions as real computed content, never
# templated prose with no substance.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Explanation:
    assessment_id: str                                  # The MarketOpportunityAssessment this explains.
    why: str                                            # Which supporting_domains + evidence produced opportunity_state.
    why_not: Tuple[str, ...]                            # Which other opportunity_states were considered and rejected, and why -- one entry per rejected candidate.
    what_changed: Optional[str]                         # Vs. the previous assessment (references previous assessment_id); None if there was no previous assessment.
    domains_agreeing: Tuple[str, ...]                    # First-class, directly populated -- never merely re-derived from supporting_domains by a caller.
    domains_disagreeing: Tuple[str, ...]                 # First-class, directly populated -- never merely re-derived from conflicting_domains by a caller.
    missing_evidence: Tuple[str, ...]                    # Which of the 9 MSI domains did NOT contribute a signal this cycle -- honestly absent, never fabricated.
    evidence_that_would_increase_confidence: Tuple[str, ...]   # Deterministic, mechanical statements only (e.g. "one more agreeing domain", "resolution of conflicting domain X") -- never a speculative/ML-style suggestion.
    schema_version: str
