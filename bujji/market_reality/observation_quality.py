"""Measured observation quality -- replaces the hardcoded `completeness=1.0`.

WHAT WAS WRONG. `build_raw_observation()` stamped `completeness=1.0`,
`missing_fields=()`, `validation_status=UNKNOWN` and `source_quality=UNKNOWN`
on EVERY observation, unconditionally. A record that admitted it had never
been validated simultaneously asserted it was 100% complete. Nothing measured
anything; the number could not change, so it carried no information -- while
reading, to every downstream consumer and every dashboard, exactly like a
measurement.

WHAT THIS MEASURES. Only what is actually observable from the payload in
hand, at capture time:

  * `missing_fields` -- REQUIRED fields for this capture kind that are
    absent or None in the payload.
  * `completeness` -- present_required / total_required. A real fraction.
  * `anomalies` -- field relationships that are impossible rather than
    merely unusual (a crossed book, a negative price, a negative quantity).
  * `validation_status` -- INVALID if anomalous, INCOMPLETE if fields are
    missing, VALID otherwise.
  * `acquisition_latency_seconds` -- exchange event time to Bujji receipt,
    ONLY where the source actually published an event time. None otherwise,
    never 0.0.

WHAT THIS DELIBERATELY DOES NOT MEASURE. Staleness relative to now, feed
silence, gaps between observations, cross-observation consistency. Those are
properties of a STREAM, not of a single observation, and asserting them from
inside one record is how a per-record field ends up lying about the feed.
They belong to the stream-level integrity checks.

REQUIRED-FIELD SETS ARE GROUNDED, NOT GUESSED. Each set below was derived by
counting field frequency across the real captured layer0 corpus, not from
FYERS documentation and not from what a field name suggests:

  QUOTE           n=2657 -- ltp present on 2657 (100%); volume/oi/prev_close
                  on 886 (33%, futures rows only) -> NOT required, because
                  requiring them would mark every spot and VIX observation
                  permanently incomplete.
  MARKET_DEPTH    n=1152 -- all 14 fields present on 100%.
  OPTION_CHAIN    all 11 extracted fields present on every stored row.
  CANDLE          OHLC by definition of the value kind.

A field that is genuinely optional for a source must never be "required"
here: that would manufacture incompleteness and is the same class of error
as manufacturing completeness.
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence, Tuple

from bujji.market_observation import taxonomy as moc_taxonomy

from . import taxonomy

# Grounded in real captured payload frequency -- see the module docstring.
REQUIRED_FIELDS_BY_KIND: dict = {
    taxonomy.KIND_MARKET_TICK: ("ltp",),
    taxonomy.KIND_QUOTE: ("ltp",),
    taxonomy.KIND_OPTION_CHAIN: ("ltp", "bid", "ask", "open_interest", "volume"),
    taxonomy.KIND_MARKET_DEPTH: ("total_buy_quantity", "total_sell_quantity", "last_price"),
    taxonomy.KIND_CANDLE: ("open", "high", "low", "close"),
}

# Fields that may never be negative. A negative traded price or a negative
# resting quantity is not a market condition -- it is a corrupt payload.
NON_NEGATIVE_FIELDS = (
    "ltp", "bid", "ask", "last_price", "open", "high", "low", "close",
    "volume", "open_interest", "total_buy_quantity", "total_sell_quantity",
)

ANOMALY_CROSSED_BOOK = "CROSSED_BOOK_BID_ABOVE_ASK"
ANOMALY_NEGATIVE_VALUE = "NEGATIVE_VALUE"
ANOMALY_IMPOSSIBLE_BAR = "IMPOSSIBLE_BAR_HIGH_BELOW_LOW"
ANOMALY_NON_NUMERIC = "NON_NUMERIC_VALUE"


@dataclass(frozen=True)
class ObservationQuality:
    """What was actually measured about one observation."""

    completeness: float
    missing_fields: Tuple[str, ...]
    validation_status: str
    anomalies: Tuple[str, ...] = ()
    acquisition_latency_seconds: Optional[float] = None

    def to_dict(self) -> dict:
        return {
            "completeness": self.completeness,
            "missing_fields": list(self.missing_fields),
            "validation_status": self.validation_status,
            "anomalies": list(self.anomalies),
            "acquisition_latency_seconds": self.acquisition_latency_seconds,
        }


def _as_number(value: Any) -> Optional[float]:
    """float(value) or None. Never raises, never coerces a bool -- `True`
    is not a price, and Python would otherwise happily read it as 1.0."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def detect_anomalies(payload: Mapping[str, Any]) -> Tuple[str, ...]:
    """Field relationships that are IMPOSSIBLE, not merely unusual.

    Deliberately narrow. A wide spread, a zero bid, a zero-volume contract
    and a stale-looking price are all real market conditions that a liquidity
    policy may reject -- but they are not corruption, and calling them
    corruption here would put a data-integrity label on a trading judgement.

    `bid == ask` is NOT flagged: a locked market is legal. `bid > ask` is,
    because a crossed book cannot be a true simultaneous quote.
    """
    found = []
    if not isinstance(payload, Mapping):
        return ()

    for name in NON_NEGATIVE_FIELDS:
        if name not in payload:
            continue
        raw = payload[name]
        if raw is None:
            continue
        number = _as_number(raw)
        if number is None:
            found.append(f"{ANOMALY_NON_NUMERIC}:{name}")
        elif number < 0:
            found.append(f"{ANOMALY_NEGATIVE_VALUE}:{name}")

    bid = _as_number(payload.get("bid"))
    ask = _as_number(payload.get("ask"))
    # Both sides must be genuinely quoted. A zero on either side means "no
    # quote on that side", which is absence, not a crossed book.
    if bid is not None and ask is not None and bid > 0 and ask > 0 and bid > ask:
        found.append(ANOMALY_CROSSED_BOOK)

    high = _as_number(payload.get("high"))
    low = _as_number(payload.get("low"))
    if high is not None and low is not None and high < low:
        found.append(ANOMALY_IMPOSSIBLE_BAR)

    return tuple(found)


def compute_acquisition_latency_seconds(
    *, event_timestamp: Optional[str], capture_timestamp: Optional[str]
) -> Optional[float]:
    """Exchange event time -> Bujji receipt, in seconds.

    None -- never 0.0 -- when the source published no event time. A zero
    latency is a MEASUREMENT of an instantaneous receipt; absence of an
    exchange clock is not, and collapsing the two would let an
    unmeasurable path read as a perfectly fast one.

    Negative values are returned as measured rather than clamped: a
    receipt timestamp earlier than the exchange's own event time is real
    evidence of clock skew between the two, and hiding it behind max(0, x)
    would destroy the only signal that skew exists.
    """
    if not event_timestamp or not capture_timestamp:
        return None
    try:
        event = datetime.datetime.fromisoformat(event_timestamp)
        received = datetime.datetime.fromisoformat(capture_timestamp)
    except (TypeError, ValueError):
        return None
    if event.tzinfo is None or received.tzinfo is None:
        # A naive timestamp cannot be compared to an aware one without
        # assuming a zone. Refuse rather than assume.
        return None
    return (received - event).total_seconds()


def assess_observation_quality(
    *,
    kind: str,
    payload: Any,
    capture_timestamp: Optional[str] = None,
    event_timestamp: Optional[str] = None,
    required_fields: Optional[Sequence[str]] = None,
) -> ObservationQuality:
    """Measure one observation. Never raises -- a quality assessment that
    can end a capture would cost the very observation it is describing.

    An UNKNOWN kind yields completeness 0.0 with validation UNKNOWN rather
    than a confident 1.0: we do not know what this record should contain,
    and that ignorance is recorded as ignorance.
    """
    expected = tuple(required_fields) if required_fields is not None \
        else REQUIRED_FIELDS_BY_KIND.get(kind, ())

    latency = compute_acquisition_latency_seconds(
        event_timestamp=event_timestamp, capture_timestamp=capture_timestamp)

    if not isinstance(payload, Mapping):
        # A non-mapping payload (an OHLC tuple, a list) cannot be field-checked
        # here. Say so; do not assert completeness over a shape we did not read.
        return ObservationQuality(
            completeness=0.0, missing_fields=(),
            validation_status=moc_taxonomy.VALIDATION_UNKNOWN,
            anomalies=(), acquisition_latency_seconds=latency)

    anomalies = detect_anomalies(payload)

    if not expected:
        # No required-field contract for this kind. Completeness is not
        # measurable, so it is not asserted -- 0.0 with UNKNOWN, never 1.0.
        return ObservationQuality(
            completeness=0.0, missing_fields=(),
            validation_status=(moc_taxonomy.VALIDATION_INVALID if anomalies
                               else moc_taxonomy.VALIDATION_UNKNOWN),
            anomalies=anomalies, acquisition_latency_seconds=latency)

    missing = tuple(name for name in expected
                    if name not in payload or payload[name] is None)
    completeness = (len(expected) - len(missing)) / len(expected)

    if anomalies:
        status = moc_taxonomy.VALIDATION_INVALID
    elif missing:
        status = moc_taxonomy.VALIDATION_INCOMPLETE
    else:
        status = moc_taxonomy.VALIDATION_VALID

    return ObservationQuality(
        completeness=completeness, missing_fields=missing,
        validation_status=status, anomalies=anomalies,
        acquisition_latency_seconds=latency)
