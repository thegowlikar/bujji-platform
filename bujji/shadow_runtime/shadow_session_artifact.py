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
