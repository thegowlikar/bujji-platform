"""Price Levels — immutable data.

Every field is either a REAL measured value or explicitly absent. Nothing
here derives anything; engine.py does the deriving, and this module only
carries results, exactly as PriceStructureAssessment does for PSI.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

from . import taxonomy


@dataclass(frozen=True)
class Bar:
    """One real OHLC bar.

    DELIBERATELY NOT a point sample. Live capture rows carry only `ltp`, and
    a level needs highs and lows -- deriving a high from a single traded
    price would invent a value that was never observed. Point samples are
    used to TEST levels (see engine.count_touches), never to form them.
    """

    timestamp: str
    open: float
    high: float
    low: float
    close: float

    @staticmethod
    def from_mapping(row: Dict[str, Any]) -> "Bar":
        """Build from a store payload. Raises on a row that is not a real
        bar -- a missing high or low is not a bar with a zero high."""
        try:
            return Bar(
                timestamp=str(row["timestamp"]),
                open=float(row["open"]), high=float(row["high"]),
                low=float(row["low"]), close=float(row["close"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"not a usable OHLC bar: {row!r} ({exc})") from exc


@dataclass(frozen=True)
class SwingPoint:
    """A pivot that survived every configured detection strength.

    `bar_index` is the identity: a pivot at bar i found under strength 3 and
    under strength 8 is the SAME swing. Matching on index rather than on a
    price tolerance avoids inventing a fudge factor to decide whether two
    detections "mean" the same thing.
    """

    bar_index: int
    timestamp: str
    price: float
    kind: str                                  # taxonomy.ALL_LEVEL_KINDS
    detected_at_strengths: Tuple[int, ...]     # every strength that saw it


@dataclass(frozen=True)
class PriceLevel:
    """One published level, with the evidence that produced it."""

    price: float
    kind: str                                  # taxonomy.ALL_LEVEL_KINDS
    formed_at: str
    formed_bar_index: int
    touch_count: int                           # REAL count of later bars entering the band
    last_touch_at: Optional[str]               # None when never revisited -- not ""
    strength: str                              # taxonomy.ALL_STRENGTHS
    detected_at_strengths: Tuple[int, ...]
    source_resolution: str
    touch_tolerance: float                     # the band actually used, in points

    def to_dict(self) -> Dict[str, Any]:
        return {
            "price": self.price, "kind": self.kind, "formed_at": self.formed_at,
            "formed_bar_index": self.formed_bar_index, "touch_count": self.touch_count,
            "last_touch_at": self.last_touch_at, "strength": self.strength,
            "detected_at_strengths": list(self.detected_at_strengths),
            "source_resolution": self.source_resolution,
            "touch_tolerance": self.touch_tolerance,
        }


@dataclass(frozen=True)
class LevelSet:
    """The answer, or an honest statement that there isn't one.

    `status` is checked FIRST by every consumer. An empty `levels` tuple with
    status AVAILABLE means "we looked and there are none"; the same tuple
    with INSUFFICIENT_HISTORY means "we could not look". Collapsing those two
    into "no levels" is the bug this field exists to prevent.
    """

    status: str                                # taxonomy.ALL_LEVELS_STATUSES
    levels: Tuple[PriceLevel, ...] = ()
    as_of: Optional[str] = None
    bars_considered: int = 0
    swings_before_agreement: int = 0           # how many the loosest parameter saw
    swings_after_agreement: int = 0            # how many survived every parameter
    strengths_required: Tuple[int, ...] = ()
    source_resolution: str = ""
    reason: Optional[str] = None
    schema_version: str = taxonomy.SCHEMA_VERSION

    @property
    def is_available(self) -> bool:
        return self.status == taxonomy.LEVELS_AVAILABLE

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status, "as_of": self.as_of,
            "levels": [lvl.to_dict() for lvl in self.levels],
            "bars_considered": self.bars_considered,
            "swings_before_agreement": self.swings_before_agreement,
            "swings_after_agreement": self.swings_after_agreement,
            "strengths_required": list(self.strengths_required),
            "source_resolution": self.source_resolution,
            "reason": self.reason, "schema_version": self.schema_version,
        }
