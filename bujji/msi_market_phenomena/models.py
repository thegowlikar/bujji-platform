"""MPC models — Series 103. Frozen dataclasses throughout (house
convention)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class MarketSnapshot:
    """A single, real, causal point-in-time reading. NOT a Production
    type -- a plain, caller-supplied translation of real Intelligence-
    layer field values (mirrors this project's established sibling-
    isolation convention, e.g. msi_strategy_selector's
    ExpressionAssessmentView). Built by translate.py from real PSI/MSSI/
    VSB/MDI assessments; engine.py never imports those types directly."""
    timestamp: str
    structure_state: str            # real PSI.structure_state
    compression_state: str          # real PSI.compression_state
    expansion_state: str            # real PSI.expansion_state
    structural_balance: str         # real MSSI.structural_balance
    structure_location: str         # real MSSI.structure_location
    volatility_regime: str          # real VSB.volatility_regime
    vsb_expansion_state: str        # real VSB.expansion_state
    vsb_compression_state: str      # real VSB.compression_state
    overall_direction: str          # real MDI.overall_direction
    overall_confidence: str         # real MDI.overall_confidence
    open_price: Optional[float]     # real, today's first real candle close/open
    previous_close_price: Optional[float]  # real, prior real trading day's close
    supporting_assessment_ids: Tuple[str, ...]  # real psi/mssi/vsb/mdi assessment_id values


@dataclass(frozen=True)
class Phenomenon:
    """One real, causally-detected market phenomenon. Never a
    recommendation, never a strategy hint -- only an objective, disclosed
    description of what real Intelligence-layer evidence showed."""
    phenomenon_id: str
    phenomenon_type: str            # taxonomy.ALL_CLASSIFIABLE_PHENOMENA_V1
    day: str
    earliest_detection_timestamp: str
    latest_confirmation_timestamp: str
    supporting_observations: Tuple[str, ...]
    evidence_references: Tuple[str, ...]   # real assessment ids, from the snapshot(s) that triggered detection.
    confidence: str                 # taxonomy.ALL_CONFIDENCE_LEVELS
    duration_seconds: Optional[float]  # real, computed from earliest/latest timestamps; None if unknown (single instant, no duration claim made).
    affected_instruments: Tuple[str, ...]
    schema_version: str


@dataclass(frozen=True)
class MarketPhenomenaReport:
    """The one real output object MPC produces for one real day. Series
    100 may consume it, Series 101 may cite it, Series 102 may replay it
    -- MPC itself recommends nothing."""
    report_id: str
    day: str
    generated_timestamp: str
    phenomena: Tuple[Phenomenon, ...]
    not_classifiable: Tuple[str, ...]   # taxonomy.ALL_NOT_CLASSIFIABLE_V1, disclosed every time, never silently omitted.
    not_classifiable_reason: str
    schema_version: str
