"""Static configuration for the Multi-Domain Consensus Intelligence
engine.

Every threshold below is fixed, disclosed configuration — never fit or
tuned against outcomes (per this project's "measure before tuning"
discipline, mirroring `bujji.msi_decision_synthesis.config`'s exact
posture). Changing a threshold is a deliberate, reviewed edit to this
file, never a runtime-learned value.
"""
from __future__ import annotations

from . import taxonomy

SCHEMA_VERSION = taxonomy.MSI_CONSENSUS_VERSION

DEFAULT_PROVENANCE = "msi_consensus.engine.compute_consensus"

# The default expected-domain registry used for coverage/missing-domain
# accounting when a caller does not supply its own. See
# `taxonomy.ALL_MSI_DOMAINS`'s docstring for why this vocabulary is
# reused BY NAME ONLY from Series 77's disclosed 9-domain list.
DEFAULT_EXPECTED_DOMAINS = taxonomy.ALL_MSI_DOMAINS

# ---------------------------------------------------------------------------
# ConsensusLevel banding — a deterministic function of `agreement_ratio`
# (agreeing_count / considered_count) ONLY. MUST be, and is, monotonic
# non-decreasing in agreement_ratio (proven by
# tests/test_msi_consensus_intelligence.py::test_consensus_level_monotonic_with_agreement_ratio).
# Zero considered domains (no directional votes cast at all) is always
# NO_CONSENSUS regardless of participation count -- there is nothing to
# agree or disagree about.
# ---------------------------------------------------------------------------
CONSENSUS_UNANIMOUS_MIN_RATIO = 1.0
CONSENSUS_STRONG_MIN_RATIO = 0.75
CONSENSUS_MODERATE_MIN_RATIO = 0.50
CONSENSUS_WEAK_MIN_RATIO = 0.0001   # any positive agreement at all, below MODERATE's band.
# ratio == 0.0 (or zero considered domains) -> NO_CONSENSUS.

# ---------------------------------------------------------------------------
# EvidenceSufficiency — a deterministic function of:
#   coverage_ratio = participating_domain_count / expected_domain_count
#   evidence_density = avg(min(1.0, len(evidence_ids) / EVIDENCE_DENSITY_TARGET)
#                          for each participating domain view)
#   sufficiency_score = coverage_ratio * evidence_density
# Deliberately independent of consensus_level/agreement (see
# taxonomy.py's module docstring on the two-dimension split).
# ---------------------------------------------------------------------------
EVIDENCE_DENSITY_TARGET = 3   # citing >= 3 evidence ids counts as "full" density for one domain.

SUFFICIENCY_ROBUST_MIN_SCORE = 0.75
SUFFICIENCY_ADEQUATE_MIN_SCORE = 0.50
SUFFICIENCY_LIMITED_MIN_SCORE = 0.0001
# score == 0.0 (or zero participating domains) -> INSUFFICIENT.

# ---------------------------------------------------------------------------
# ConfidenceCalibration — per-domain-view judgment thresholds, then a
# majority vote across every JUDGED domain (a domain with zero cited
# evidence ids cannot be judged at all and is excluded from the vote,
# never defaulted to WELL_CALIBRATED).
# ---------------------------------------------------------------------------
CALIBRATION_HIGH_CONFIDENCE_MIN = 0.70    # confidence >= this counts as "claims high confidence."
CALIBRATION_LOW_CONFIDENCE_MAX = 0.40     # confidence <= this counts as "claims low confidence."
CALIBRATION_THIN_EVIDENCE_MAX_COUNT = 1   # <= this many evidence ids counts as "thin."
CALIBRATION_DENSE_EVIDENCE_MIN_COUNT = 3  # >= this many evidence ids counts as "dense."

# ---------------------------------------------------------------------------
# contradiction_density — Deliverable 2's field.
# Formula (disclosed, exact): conflicting_domain_count / considered_domain_count,
# or 0.0 when there are zero considered domains. This is the same
# denominator (considered domains) used for agreement_ratio, so
# contradiction_density + agreement_ratio == 1.0 whenever considered
# domains > 0 (they are complementary views of the same vote).
# ---------------------------------------------------------------------------
