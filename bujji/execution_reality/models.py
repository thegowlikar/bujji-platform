"""Execution Reality Layer -- Phase-0 data contracts.

`LegQuote` and `QuoteObservationRecord` only. No decision-shaped field
exists on either type (no `allowed`, no `approved`, no `recommended`) --
this is deliberate and structural, not just a naming convention: a
Phase-0 consumer has nothing to wire into a trading decision even by
mistake, because neither type has anywhere to put one.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from bujji.core.enums import Side

# data_quality vocabulary. Plain string constants, matching this
# codebase's established convention for closed, small vocabularies
# (ACTION_*/STATUS_*/HEALTH_* throughout production_runtime/ and
# trading_brain/) rather than a new Enum class for a single field.
DATA_QUALITY_LIVE_QUOTE = "LIVE_QUOTE"
DATA_QUALITY_UNAVAILABLE = "UNAVAILABLE"
DATA_QUALITY_STALE = "STALE"
DATA_QUALITY_INVALID = "INVALID"

_VALID_DATA_QUALITIES = (
    DATA_QUALITY_LIVE_QUOTE, DATA_QUALITY_UNAVAILABLE, DATA_QUALITY_STALE, DATA_QUALITY_INVALID,
)

# raw_source_status vocabulary -- the RAW outcome of the broker call,
# recorded before any LegQuote normalization judgment is applied.
RAW_STATUS_RESPONDED = "RESPONDED"
RAW_STATUS_NONE_RETURNED = "NONE_RETURNED"
RAW_STATUS_CALL_FAILED = "CALL_FAILED"

_VALID_RAW_STATUSES = (RAW_STATUS_RESPONDED, RAW_STATUS_NONE_RETURNED, RAW_STATUS_CALL_FAILED)


@dataclass(frozen=True)
class LegQuote:
    """One option leg's quote reality at one point in time. Every
    field is exactly what was observed or explicitly None -- never a
    guessed, cached, or fabricated value. `mid`/`absolute_spread`/
    `spread_percentage` are populated ONLY when data_quality is
    LIVE_QUOTE or STALE (both mean "the bid/ask themselves were
    valid") -- never for UNAVAILABLE/INVALID, where computing them
    from bad or missing inputs would misrepresent the underlying
    data as more complete than it is."""

    symbol: str
    strike: float
    option_type: str          # "CE" / "PE" -- plain string, per the frozen Phase-0 contract
    side: Side                 # existing bujji.core.enums.Side -- no duplicate definition

    bid: Optional[float]
    ask: Optional[float]

    mid: Optional[float]
    absolute_spread: Optional[float]
    spread_percentage: Optional[float]

    data_quality: str
    timestamp: Optional[str]   # ISO timestamp of the broker observation this LegQuote reflects

    def __post_init__(self) -> None:
        if self.data_quality not in _VALID_DATA_QUALITIES:
            raise ValueError(f"data_quality must be one of {_VALID_DATA_QUALITIES}, got {self.data_quality!r}")


@dataclass(frozen=True)
class QuoteObservationRecord:
    """Forensic record of one quote-capture attempt: what was asked
    for, what the broker actually returned (raw_source_status), and
    how the Market Quote Adapter normalized it (normalized_result).

    NOT ExecutionQualityJournal (that type does not exist yet, and
    this is not a substitute for it): this record has no concept of
    an order, a fill, an expected price, or a strategy -- it only ever
    answers "what did a quote call return, and did the adapter
    interpret it correctly." ExecutionQualityJournal's own future
    schema (docs/EXECUTION_INTELLIGENCE_PHASE2_DESIGN.md) presupposes
    a real trade was constructed and filled; Phase-0 has neither."""

    symbol: str
    exchange: str
    expiry: str
    strike: float
    option_type: str
    side: Side

    timestamp: str              # when THIS ARTIFACT was recorded (distinct from LegQuote.timestamp,
                                 # which is the broker OBSERVATION time -- see the adapter's own docstring)
    broker_source: str          # e.g. "FyersBroker"
    raw_source_status: str
    normalized_result: LegQuote

    def __post_init__(self) -> None:
        if self.raw_source_status not in _VALID_RAW_STATUSES:
            raise ValueError(
                f"raw_source_status must be one of {_VALID_RAW_STATUSES}, got {self.raw_source_status!r}"
            )
