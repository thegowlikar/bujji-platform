"""bujji.msi_position_recomposition.runner — Series 110.

Thin batch/streaming wrapper -- pure orchestration of `engine
.recompose_position`, no new logic.
"""
from __future__ import annotations

from typing import Any, Optional, Sequence

from . import engine


def run_recomposition(
    *, board: Any, held_legs: Sequence, strategy_family: str, chain: Sequence, spot: Optional[float],
    day: str, direction: Optional[str] = None, expected_move_pct: Optional[float] = None, timestamp: str,
):
    return engine.recompose_position(
        board, held_legs, strategy_family, chain, spot, day,
        direction=direction, expected_move_pct=expected_move_pct, timestamp=timestamp,
    )
