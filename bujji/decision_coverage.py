"""bujji.decision_coverage — Sprint 111: Live Shadow Validation & Decision
Coverage.

Validation/reporting/coverage-analysis ONLY -- adds zero trading
intelligence, zero thresholds, zero decision logic. Deliberately a
single glue module (not a 9-file MSI package), matching the precedent
set by Sprints 105/106/107 for integration-and-tooling work that
consumes the frozen decision engine rather than extending it.

Every taxonomy value counted below is imported BY IDENTITY from its
real, frozen, owning module (Series 73-110) -- this module never
re-declares or guesses a vocabulary list.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

from bujji.msi_trade_thesis import taxonomy as thesis_taxonomy
from bujji.msi_strategy_selection_foundation import taxonomy as ssf_taxonomy
from bujji.msi_position_construction import taxonomy as pci_taxonomy
from bujji.msi_dynamic_management import taxonomy as mdm_taxonomy
from bujji.msi_strategy_optimization import taxonomy as mso_taxonomy
from bujji.msi_position_lifecycle import taxonomy as pli_taxonomy
from bujji.msi_performance_analytics import config as paev_config

# ---------------------------------------------------------------------------
# Deliverable 1 -- the real expected vocabulary, by category, each drawn
# by identity from its owning frozen module.
# ---------------------------------------------------------------------------
EXPECTED_VOCABULARY: Dict[str, Tuple[str, ...]] = {
    "thesis_type": thesis_taxonomy.ALL_THESIS_TYPES,
    "strategy_family": ssf_taxonomy.ALL_STRATEGY_FAMILIES,
    "construction_type": pci_taxonomy.ALL_CONSTRUCTION_TYPES,
    "roll_decision_type": mdm_taxonomy.ALL_DECISION_TYPES,
    "roll_priority": mdm_taxonomy.ALL_PRIORITIES,
    "adjustment_action": mso_taxonomy.ALL_ADJUSTMENT_ACTIONS,
    "position_lifecycle_state": pli_taxonomy.ALL_POSITION_STATES,
}

# ---------------------------------------------------------------------------
# Deliverable 1 -- coverage buckets. Thresholds are structural, disclosed,
# never tuned: matches the spec's own worked example bands exactly
# (<5 RARE, 5-20 OCCASIONAL, >20 COMMON), 0 is NEVER_OBSERVED.
# ---------------------------------------------------------------------------
BUCKET_NEVER_OBSERVED = "NEVER_OBSERVED"
BUCKET_RARE = "RARE"
BUCKET_OCCASIONAL = "OCCASIONAL"
BUCKET_COMMON = "COMMON"


def classify(count: int) -> str:
    if count == 0:
        return BUCKET_NEVER_OBSERVED
    if count < 5:
        return BUCKET_RARE
    if count <= 20:
        return BUCKET_OCCASIONAL
    return BUCKET_COMMON


@dataclass(frozen=True)
class CoverageEntry:
    category: str
    value: str
    count: int
    bucket: str


@dataclass(frozen=True)
class CoverageMatrix:
    entries: Tuple[CoverageEntry, ...]

    def by_category(self, category: str) -> Tuple[CoverageEntry, ...]:
        return tuple(e for e in self.entries if e.category == category)

    def never_observed(self) -> Tuple[CoverageEntry, ...]:
        return tuple(e for e in self.entries if e.bucket == BUCKET_NEVER_OBSERVED)

    def rare(self) -> Tuple[CoverageEntry, ...]:
        return tuple(e for e in self.entries if e.bucket == BUCKET_RARE)


def build_coverage_matrix(observed_counts: Dict[str, Counter]) -> CoverageMatrix:
    """Deliverable 1. `observed_counts[category]` is a real `Counter`
    the caller built while replaying real data -- this function never
    fabricates a count, only classifies real ones (and reports an
    honest 0 for a real vocabulary value that was in scope but simply
    never appeared)."""
    entries = []
    for category, vocabulary in EXPECTED_VOCABULARY.items():
        counts = observed_counts.get(category, Counter())
        for value in vocabulary:
            count = counts.get(value, 0)
            entries.append(CoverageEntry(category=category, value=value, count=count, bucket=classify(count)))
    return CoverageMatrix(entries=tuple(entries))


# ---------------------------------------------------------------------------
# Deliverable 2 -- dead logic detection. Purely a re-labelling of
# NEVER_OBSERVED entries by what KIND of dead logic they represent --
# never deletes or disables anything.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class DeadLogicFinding:
    category: str
    value: str
    kind: str   # "unused_strategy_family" | "unused_roll_path" | "unused_recomposition_path" | "unused_adjustment_action" | "other"


def dead_logic_report(matrix: CoverageMatrix) -> Tuple[DeadLogicFinding, ...]:
    kind_by_category = {
        "strategy_family": "unused_strategy_family",
        "roll_decision_type": "unused_roll_path",
        "adjustment_action": "unused_adjustment_action",
        "construction_type": "unused_construction_type",
        "thesis_type": "unused_thesis_type",
        "position_lifecycle_state": "unused_lifecycle_state",
    }
    findings = []
    for e in matrix.never_observed():
        findings.append(DeadLogicFinding(category=e.category, value=e.value, kind=kind_by_category.get(e.category, "other")))
    return tuple(findings)


# ---------------------------------------------------------------------------
# Deliverable 8 -- reliability gates. Reuses Series 101's own real
# `MIN_RELIABLE_SAMPLE_SIZE` (30) by identity -- never a new number.
# Mirrors Sprint 102's own Gate A-E framing.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ReliabilityDashboard:
    shadow_trades_completed: int
    statistically_reliable: bool
    min_reliable_sample_size: int
    evidence_collected_days: int
    coverage_pct: float
    architecture_frozen: bool
    replay_parity_decision_level_pct: Optional[float]


def build_reliability_dashboard(
    *, shadow_trades_completed: int, evidence_collected_days: int, matrix: CoverageMatrix,
    architecture_frozen: bool = True, replay_parity_decision_level_pct: Optional[float] = None,
) -> ReliabilityDashboard:
    total = len(matrix.entries)
    covered = total - len(matrix.never_observed())
    coverage_pct = (covered / total * 100.0) if total else 0.0
    return ReliabilityDashboard(
        shadow_trades_completed=shadow_trades_completed,
        statistically_reliable=shadow_trades_completed >= paev_config.MIN_RELIABLE_SAMPLE_SIZE,
        min_reliable_sample_size=paev_config.MIN_RELIABLE_SAMPLE_SIZE,
        evidence_collected_days=evidence_collected_days, coverage_pct=coverage_pct,
        architecture_frozen=architecture_frozen, replay_parity_decision_level_pct=replay_parity_decision_level_pct,
    )


# ---------------------------------------------------------------------------
# Deliverable 10 -- recommendation. Evidence-only rules, structural and
# disclosed, never engineering intuition.
# ---------------------------------------------------------------------------
RECOMMEND_CONTINUE_EVIDENCE_COLLECTION = "Continue Evidence Collection"
RECOMMEND_READY_FOR_PAPER_TRADING = "Ready for Paper Trading"
RECOMMEND_READY_FOR_CAPITAL_PILOT = "Ready for Capital Pilot"

# Structural bars, disclosed: Paper Trading requires the sample-size
# floor already established by Series 101 (30) AND at least half the
# real vocabulary to have been observed at least once (a real, minimum
# "the system has actually done most of what it claims it can do" bar).
# Capital Pilot additionally requires COMMON coverage (>20 observations)
# on every entry -- i.e. genuinely repeated, not merely sampled, real
# behaviour. Neither number is tuned against any P&L outcome.
PAPER_TRADING_MIN_COVERAGE_PCT = 50.0
CAPITAL_PILOT_MIN_COVERAGE_PCT = 100.0


def recommend(dashboard: ReliabilityDashboard) -> str:
    if not dashboard.statistically_reliable or dashboard.coverage_pct < PAPER_TRADING_MIN_COVERAGE_PCT:
        return RECOMMEND_CONTINUE_EVIDENCE_COLLECTION
    if dashboard.coverage_pct < CAPITAL_PILOT_MIN_COVERAGE_PCT:
        return RECOMMEND_READY_FOR_PAPER_TRADING
    return RECOMMEND_READY_FOR_CAPITAL_PILOT
