"""Price Levels — where structure sits, numerically.

WHY THIS PACKAGE EXISTS. `msi_price_structure` answers "what SHAPE is price
in" -- RANGING, TRENDING, swing CONFIRMED -- and every field it publishes is
categorical. Nothing in Bujji knew, at any price, WHERE that structure was.
For a premium seller that is the decision-relevant fact: strike selection and
the whole short-strangle thesis depend on how far price can plausibly travel
before it meets something, and until now nothing could say.

This package is additive and observation-only. It publishes levels; it does
not select strikes, size positions, or influence any existing decision. That
step is an operator gate, not an implementation detail.
"""
from .context import LevelContext, build_level_context
from .daily import (LevelsSnapshot, build_snapshot, latest_snapshot_path,
                    read_snapshot_raw, snapshot_age_days, snapshot_path, write_snapshot)
from .live import apply_sample, apply_sample_to_levels, apply_sample_to_zones
from .store_reader import BarLoadResult, load_bars
from .engine import count_touches, detect_levels, detect_zones
from .models import Bar, LevelSet, PriceLevel, SupplyDemandZone, SwingPoint, ZoneSet
from .swings import detect_swings_at_strength, swings_surviving_agreement
from .zones import build_zones, track_zone, true_range, typical_range

__all__ = [
    "Bar", "LevelSet", "PriceLevel", "SwingPoint", "SupplyDemandZone", "ZoneSet",
    "LevelContext", "build_level_context",
    "LevelsSnapshot", "build_snapshot", "write_snapshot", "read_snapshot_raw",
    "latest_snapshot_path", "snapshot_path", "snapshot_age_days",
    "apply_sample", "apply_sample_to_levels", "apply_sample_to_zones",
    "BarLoadResult", "load_bars",
    "detect_levels", "detect_zones", "count_touches",
    "detect_swings_at_strength", "swings_surviving_agreement",
    "build_zones", "track_zone", "true_range", "typical_range",
]
