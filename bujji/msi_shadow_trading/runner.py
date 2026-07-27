"""Shadow Trading Engine runner — Series 100. Dual batch/streaming
entrypoints, proven byte-identical by test (house convention)."""
from __future__ import annotations

from typing import List, Sequence, Tuple

from .engine import open_shadow_position, track_shadow_position
from .journal import ShadowTradingJournal
from .models import ShadowPosition


def open_positions_batch(requests: Sequence[dict]) -> Tuple[ShadowPosition, ...]:
    return tuple(open_shadow_position(**req) for req in requests)


class ShadowTradingStream:
    def __init__(self) -> None:
        self.journal = ShadowTradingJournal()
        self._positions: List[ShadowPosition] = []

    def open(self, **kwargs) -> ShadowPosition:
        position = open_shadow_position(**kwargs)
        self._positions.append(position)
        self.journal.record_open(position, recorded_at=kwargs["timestamp"])
        return position

    def track(self, position: ShadowPosition, **kwargs) -> ShadowPosition:
        updated = track_shadow_position(position, **kwargs)
        idx = self._positions.index(position) if position in self._positions else None
        if idx is not None:
            self._positions[idx] = updated
        self.journal.record_tracking_update(updated, recorded_at=kwargs["timestamp"])
        return updated

    def positions(self) -> Tuple[ShadowPosition, ...]:
        return tuple(self._positions)
