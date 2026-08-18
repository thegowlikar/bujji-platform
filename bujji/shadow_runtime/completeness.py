"""End-of-Day Completeness Validation -- Shadow Runtime, Phase 19.11.

Answers "did today's capture actually produce a usable Reality view" --
read-only, never writes anything, never recomputes what
`build_market_reality_snapshot()` (Phase 18.1, unmodified) already
computes. Reuses that function directly for spot/options/vix presence
and certification status, rather than re-deriving them from raw
`HistoricalObservationStore` rows a second time.
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass
from typing import Optional, Tuple

from bujji.historical_reality.store import HistoricalObservationStore
from bujji.market_reality_snapshot.builder import build_market_reality_snapshot
from bujji.market_reality_snapshot.models import COMPLETENESS_EMPTY, RESOLUTION_DAILY

FUTURES_STATUS_PRESENT = "PRESENT"
FUTURES_STATUS_ABSENT = "ABSENT"
FUTURES_STATUS_UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class EndOfDayCompletenessReport:
    date: str
    spot_present: bool
    options_present: bool
    vix_present: bool
    futures_status: str
    certification_refs: Tuple[str, ...]
    completeness: str  # market_reality_snapshot.models.ALL_COMPLETENESS_STATES
    is_complete: bool

    def to_dict(self) -> dict:
        return {
            "date": self.date, "spot_present": self.spot_present, "options_present": self.options_present,
            "vix_present": self.vix_present, "futures_status": self.futures_status,
            "certification_refs": list(self.certification_refs),
            "completeness": self.completeness, "is_complete": self.is_complete,
        }


def validate_end_of_day_completeness(
    date: str, *, historical_store: HistoricalObservationStore, now: datetime.datetime,
    resolution: str = RESOLUTION_DAILY, as_of_time: Optional[str] = None,
) -> EndOfDayCompletenessReport:
    """`now` is caller-injected -- no wall-clock read anywhere in this
    module, same discipline every other Phase 18/19 component follows.

    `resolution`/`as_of_time` (Phase 19.14.4, additive, defaulting to the
    exact prior behavior): `build_market_reality_snapshot()` has always
    supported `resolution=RESOLUTION_FIVE_MINUTE` (Phase 18.1), but this
    module never exposed it -- it always called with the RESOLUTION_DAILY
    default. Phase 19.14.3's own production re-verification found this
    was a genuine defect for the ONLY caller that matters in the
    autonomous daily path (`run_daily_intelligence_session.py`):
    `capture_market_reality_session.py`/`capture_options_reality_session.py`
    (the sole writers, unmodified) always write `resolution=FIVE_MINUTE`
    rows -- never `DAILY` -- so a `RESOLUTION_DAILY` query here always
    found zero rows and reported every real, successfully-captured
    trading day as `EMPTY`/incomplete. Defaulting `resolution` to
    `RESOLUTION_DAILY` here keeps every OTHER existing caller (including
    this module's own prior tests) behaving byte-for-byte identically;
    only a caller that explicitly opts into `RESOLUTION_FIVE_MINUTE` (and
    supplies the now-required `as_of_time`) gets the fix."""
    snapshot = build_market_reality_snapshot(
        date, historical_store=historical_store, now=now, resolution=resolution, as_of_time=as_of_time,
    )

    spot_present = snapshot.spot is not None
    options_present = snapshot.options is not None and len(snapshot.options.contracts) > 0
    vix_present = snapshot.vix is not None
    futures_status = FUTURES_STATUS_PRESENT if snapshot.futures is not None else (
        FUTURES_STATUS_ABSENT if snapshot.completeness != COMPLETENESS_EMPTY else FUTURES_STATUS_UNKNOWN
    )

    is_complete = spot_present and options_present and vix_present

    return EndOfDayCompletenessReport(
        date=date, spot_present=spot_present, options_present=options_present, vix_present=vix_present,
        futures_status=futures_status, certification_refs=snapshot.certification_refs,
        completeness=snapshot.completeness, is_complete=is_complete,
    )
