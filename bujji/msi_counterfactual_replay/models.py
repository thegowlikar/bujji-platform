"""CRE models — Series 102. Frozen dataclasses throughout (house
convention)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class ExploredPath:
    """One real, causal decision path -- the real, unmodified frozen
    Production pipeline's real output, truncated to only the real data
    available at `decision_timestamp`. Never a fabricated trade, strike,
    or timing -- every field here is a real pass-through of what
    `bujji.msi_counterfactual_replay.replay.run_real_path` actually
    observed."""
    path_id: str
    label: str                             # taxonomy.ALL_PATH_LABELS
    decision_timestamp: str
    thesis_type: Optional[str]
    selected_family: Optional[str]
    data_timestamps_used: Tuple[str, ...]  # every real candle timestamp actually fed into this path -- the causality validator's own evidence.
    rationale: Tuple[str, ...]
    schema_version: str


@dataclass(frozen=True)
class CounterfactualSession:
    """The one real output object CRE produces. MLE consumes it, Evidence
    Packets cite it, Knowledge Candidates interpret it -- CRE itself
    interprets nothing."""
    session_id: str
    replay_id: str
    replay_timestamp: str                  # the ALTERNATIVE path's real decision_timestamp.
    baseline_timestamp: str                # the BASELINE (real Production) path's real decision_timestamp.
    production_version: str
    replay_version: str
    assumptions: Tuple[str, ...]
    explored_paths: Tuple[ExploredPath, ...]
    rejected_paths: Tuple[str, ...]        # disclosed reasoning for paths NOT explored (Phase 1 scope), never fabricated alternatives.
    replay_legality: str                   # taxonomy.ALL_LEGALITY_STATES
    legality_reasoning: Tuple[str, ...]
    causality_verdicts: Tuple[str, ...]    # per-path disclosed causality check results.
    earliest_causal_timestamp: str
    supporting_references: Tuple[str, ...]  # real Series 99 decision_id/outcome_id references, or real journal file paths.
    schema_version: str
