"""Shadow Session Artifact -- Shadow Runtime, Phase-5.

The one summary artifact a shadow observation session produces. No
P&L, no trade approval, no execution result -- no field exists
anywhere in this module for any of those; it summarizes what was
OBSERVED, never what was DECIDED.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple


@dataclass(frozen=True)
class ShadowSessionArtifact:
    session_id: str
    start_time: str
    end_time: str
    observations_count: int
    data_quality_summary: Dict[str, int]
    liquidity_summary: Optional[Dict[str, int]]
    errors: Tuple[str, ...]
    runtime_health: Dict[str, Any]
    # Phase 15C -- Runtime Recovery Integration. None when recovery was
    # not requested for this run (regime_memory_event_store_path unset);
    # a real RecoveryReport.to_dict() when it was. Defaulted so every
    # pre-Phase-15C caller/test constructing this dataclass positionally
    # or by keyword without this field keeps working unchanged.
    recovery_report: Optional[Dict[str, Any]] = None
    # Phase 15D -- Observation Memory Recovery. None when recovery was
    # not requested (observation_memory_recovery_enabled=False, the
    # default); a real RecoveryReport.to_dict() when it was. Kept as
    # its own field, separate from `recovery_report` (regime memory),
    # so existing Phase 15C callers/tests reading that field's shape
    # are completely unaffected.
    observation_memory_recovery_report: Optional[Dict[str, Any]] = None
    # Phase 15E -- Premium Behaviour Recovery. Same defaulted, additive
    # pattern as the two fields above.
    premium_behaviour_recovery_report: Optional[Dict[str, Any]] = None
