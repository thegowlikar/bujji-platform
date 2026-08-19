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


def _level_from_dict(d: Dict[str, Any]) -> "PriceLevel":
    """Rebuild a level from a persisted snapshot.

    Strict on the fields that carry meaning: a snapshot missing a price or a
    touch count is a corrupt snapshot, and reading it as a level with price
    zero would put a fabricated support into a decision record.
    """
    return PriceLevel(
        price=float(d["price"]), kind=d["kind"], formed_at=d["formed_at"],
        formed_bar_index=int(d["formed_bar_index"]), touch_count=int(d["touch_count"]),
        last_touch_at=d.get("last_touch_at"), strength=d["strength"],
        detected_at_strengths=tuple(d.get("detected_at_strengths", ())),
        source_resolution=d.get("source_resolution", ""),
        touch_tolerance=float(d["touch_tolerance"]),
    )


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

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "LevelSet":
        return LevelSet(
            status=d["status"],
            levels=tuple(_level_from_dict(x) for x in d.get("levels", ())),
            as_of=d.get("as_of"), bars_considered=int(d.get("bars_considered", 0)),
            swings_before_agreement=int(d.get("swings_before_agreement", 0)),
            swings_after_agreement=int(d.get("swings_after_agreement", 0)),
            strengths_required=tuple(d.get("strengths_required", ())),
            source_resolution=d.get("source_resolution", ""), reason=d.get("reason"),
            schema_version=d.get("schema_version", taxonomy.SCHEMA_VERSION),
        )

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


@dataclass(frozen=True)
class SupplyDemandZone:
    """A band price left in a hurry, and what became of it.

    `lower`/`upper` are the origin bar's own observed low and high -- a range
    price really traded in, never a widened or smoothed band.
    """

    lower: float
    upper: float
    kind: str                                  # taxonomy.ALL_ZONE_KINDS
    formed_at: str
    formed_bar_index: int
    impulse_size: float                        # measured move that created it, in points
    status: str                                # taxonomy.ALL_ZONE_STATUSES
    test_count: int                            # REAL count of later bars entering the band
    last_test_at: Optional[str]                # None when never revisited -- not ""
    broken_at: Optional[str]                   # None unless price CLOSED through
    detected_at_multiples: Tuple[float, ...]
    source_resolution: str

    @property
    def midpoint(self) -> float:
        return (self.lower + self.upper) / 2.0

    @property
    def height(self) -> float:
        return self.upper - self.lower

    def to_dict(self) -> Dict[str, Any]:
        return {
            "lower": self.lower, "upper": self.upper, "kind": self.kind,
            "formed_at": self.formed_at, "formed_bar_index": self.formed_bar_index,
            "impulse_size": self.impulse_size, "status": self.status,
            "test_count": self.test_count, "last_test_at": self.last_test_at,
            "broken_at": self.broken_at,
            "detected_at_multiples": list(self.detected_at_multiples),
            "source_resolution": self.source_resolution,
        }


@dataclass(frozen=True)
class ZoneSet:
    """The zones, or an honest statement that there are none to give.

    Same contract as LevelSet: `status` is checked FIRST. An empty tuple
    under AVAILABLE means "we looked and found none"; under
    INSUFFICIENT_HISTORY it means "we could not look".
    """

    status: str                                # taxonomy.ALL_LEVELS_STATUSES (shared vocabulary)
    zones: Tuple[SupplyDemandZone, ...] = ()
    as_of: Optional[str] = None
    bars_considered: int = 0
    zones_before_agreement: int = 0
    zones_after_agreement: int = 0
    multiples_required: Tuple[float, ...] = ()
    source_resolution: str = ""
    reason: Optional[str] = None
    schema_version: str = taxonomy.SCHEMA_VERSION

    @property
    def is_available(self) -> bool:
        return self.status == taxonomy.LEVELS_AVAILABLE

    @property
    def live_zones(self) -> Tuple["SupplyDemandZone", ...]:
        """Zones whose claim has not yet failed. A BROKEN zone is kept in
        `zones` deliberately -- it is real history, and a reader asking
        "what has already failed here" is asking a legitimate question."""
        return tuple(z for z in self.zones if z.status != taxonomy.ZONE_BROKEN)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status, "as_of": self.as_of,
            "zones": [z.to_dict() for z in self.zones],
            "bars_considered": self.bars_considered,
            "zones_before_agreement": self.zones_before_agreement,
            "zones_after_agreement": self.zones_after_agreement,
            "multiples_required": list(self.multiples_required),
            "source_resolution": self.source_resolution,
            "reason": self.reason, "schema_version": self.schema_version,
        }


def _zone_from_dict(d: Dict[str, Any]) -> "SupplyDemandZone":
    """Rebuild a zone from a persisted snapshot. Strict for the same reason
    levels are: a band with a missing edge is not a band."""
    return SupplyDemandZone(
        lower=float(d["lower"]), upper=float(d["upper"]), kind=d["kind"],
        formed_at=d["formed_at"], formed_bar_index=int(d["formed_bar_index"]),
        impulse_size=float(d.get("impulse_size", 0.0)), status=d["status"],
        test_count=int(d.get("test_count", 0)), last_test_at=d.get("last_test_at"),
        broken_at=d.get("broken_at"),
        detected_at_multiples=tuple(d.get("detected_at_multiples", ())),
        source_resolution=d.get("source_resolution", ""),
    )


def zone_set_from_dict(d: Dict[str, Any]) -> "ZoneSet":
    return ZoneSet(
        status=d["status"], zones=tuple(_zone_from_dict(x) for x in d.get("zones", ())),
        as_of=d.get("as_of"), bars_considered=int(d.get("bars_considered", 0)),
        zones_before_agreement=int(d.get("zones_before_agreement", 0)),
        zones_after_agreement=int(d.get("zones_after_agreement", 0)),
        multiples_required=tuple(d.get("multiples_required", ())),
        source_resolution=d.get("source_resolution", ""), reason=d.get("reason"),
        schema_version=d.get("schema_version", taxonomy.SCHEMA_VERSION),
    )
