"""Trade Thesis Engine models — Series 92. Frozen dataclasses throughout
(house convention)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class Explanation:
    assessment_id: str
    why_this_thesis: Tuple[str, ...]
    supporting_evidence: Tuple[str, ...]
    conflicting_evidence: Tuple[str, ...]
    what_would_invalidate: Tuple[str, ...]
    schema_version: str


@dataclass(frozen=True)
class TradeThesisAssessment:
    assessment_id: str
    timestamp: str
    thesis_type: str                        # taxonomy.ALL_THESIS_TYPES
    market_expectation: str                 # One-sentence, deterministic, plain-language statement.
    expected_move: Optional[float]          # Real VSB expected_move_pct pass-through, None if unavailable.
    expected_time_horizon: str              # taxonomy.ALL_TIME_HORIZONS
    volatility_expectation: str             # taxonomy.ALL_VOLATILITY_EXPECTATIONS
    directional_expectation: str            # Real MDI overall_direction pass-through.
    conviction: str                         # taxonomy.ALL_CONVICTION_LEVELS
    invalidation_conditions: Tuple[str, ...]
    supporting_domains: Tuple[str, ...]     # taxonomy.ALL_DOMAINS subset.
    conflicting_domains: Tuple[str, ...]    # taxonomy.ALL_DOMAINS subset.
    explanation: Explanation
    provenance: str
    schema_version: str
