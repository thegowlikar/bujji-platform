"""The typed quote: what one SDK callback said, with provenance per field.

WHY THIS TYPE EXISTS. The runtime's market-data representation was
`{symbol: float}` -- the latest LTP and nothing else. Bid, ask, sizes,
exchange time, volume and open interest were read off the callback, dropped,
and could never reach a decision however carefully they were captured
upstream. The choke point was never storage; it was this contract.

WHY IT LIVES IN `market_perception`. That package already owns the analytical
vocabulary -- `OptionLeg` carries ltp/bid/ask/spread/volume/open_interest as
Optionals, `MarketSnapshot` carries health and missing fields. A quote is the
arrival-grained member of the SAME family, and `to_option_leg()` projects into
it. Putting this anywhere else would start a second model family, which is
precisely what this migration exists to prevent.

THREE RULES.

  UNAVAILABLE IS NOT ZERO. A field the SDK did not send is `None` with
  availability UNAVAILABLE. Zero is a price, a size, an open interest -- it
  says the market had none, which is a claim we cannot make about a field we
  never received.

  PROVENANCE TRAVELS WITH THE VALUE, not with the batch. A snapshot may mix a
  live tick with a REST fallback, and a decision that treats those as
  interchangeable is a decision made on evidence it cannot describe. Every
  field carries where it came from and when.

  FRESHNESS IS MONOTONIC. Age is computed from `recv_mono`, never from wall
  clock. A wall-clock step would otherwise make a stale quote look fresh, or
  a fresh one look stale, and neither is recoverable afterwards.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional, Tuple

# -- availability ----------------------------------------------------------
AVAILABLE = "AVAILABLE"
UNAVAILABLE = "UNAVAILABLE"          # the source did not supply this field
NULL_REPORTED = "NULL_REPORTED"      # the source supplied it, explicitly empty

# -- provenance ------------------------------------------------------------
SOURCE_TICK = "LIVE_TICK"            # a websocket callback
SOURCE_REST = "REST_FALLBACK"        # a per-symbol REST read
SOURCE_REPLAY = "REPLAY"             # journal replay
SOURCE_UNKNOWN = "UNKNOWN"
ALL_SOURCES = (SOURCE_TICK, SOURCE_REST, SOURCE_REPLAY, SOURCE_UNKNOWN)

# ---------------------------------------------------------------------------
# THE ONE PROVISIONAL SDK BOUNDARY.
#
# Every assumption about what FYERS names a field lives here and nowhere else,
# so that Monday's Gate 1 measurement can correct it in ONE place rather than
# in scattered `msg.get(...)` calls across the runtime.
#
# PROVISIONAL, AND THAT WORD IS LOAD-BEARING. These keys are taken from SDK
# documentation and from the shapes the codebase already reads. NONE of them
# has been observed in a captured full-mode payload, because no capture has
# ever run. A key that turns out to be wrong yields UNAVAILABLE -- never a
# wrong number and never a zero -- which is the failure mode this indirection
# is chosen for.
#
# Gate 1 produces `field_availability` for every key it actually sees. After
# Monday, reconcile this map against that evidence before any paper session
# depends on a field.
# ---------------------------------------------------------------------------
PROVISIONAL_SDK_FIELD_MAP: Dict[str, Tuple[str, ...]] = {
    "ltp": ("ltp",),
    "bid": ("bid_price", "bid"),
    "ask": ("ask_price", "ask"),
    "bid_size": ("bid_size", "bid_qty"),
    "ask_size": ("ask_size", "ask_qty"),
    "volume": ("vol_traded_today", "volume"),
    "open_interest": ("open_interest", "oi"),
    "prev_open_interest": ("prev_oi",),
    "exchange_time": ("exch_feed_time", "last_traded_time"),
    "last_traded_qty": ("last_traded_qty",),
    "total_buy_qty": ("tot_buy_qty",),
    "total_sell_qty": ("tot_sell_qty",),
}
PROVISIONAL_DEPTH_KEYS: Tuple[str, ...] = ("bids", "asks", "market_depth", "depth")

FIELD_MAP_STATUS = "PROVISIONAL_UNVERIFIED"


@dataclass(frozen=True)
class FieldValue:
    """One field, with where it came from and when it was true."""

    value: Optional[float]
    availability: str
    source: str
    observed_wall: Optional[float] = None
    observed_mono: Optional[float] = None
    sdk_key: Optional[str] = None          # which key actually supplied it

    @property
    def is_available(self) -> bool:
        return self.availability == AVAILABLE and self.value is not None

    def age_seconds(self, now_mono: float) -> Optional[float]:
        """MONOTONIC ONLY. Wall clock can step; a stepped clock would report a
        stale quote as fresh, and nothing downstream could tell."""
        if self.observed_mono is None:
            return None
        return max(0.0, now_mono - self.observed_mono)

    def as_dict(self) -> Dict[str, Any]:
        return {"value": self.value, "availability": self.availability,
                "source": self.source, "observed_wall": self.observed_wall,
                "sdk_key": self.sdk_key}


def unavailable(source: str = SOURCE_UNKNOWN) -> FieldValue:
    """The only way to express 'we do not have this'. Never 0.0."""
    return FieldValue(value=None, availability=UNAVAILABLE, source=source)


@dataclass(frozen=True)
class Quote:
    """One instrument's state as one source described it at one instant."""

    symbol: str
    fields: Mapping[str, FieldValue]
    recv_wall: Optional[float] = None
    recv_mono: Optional[float] = None
    sequence: Optional[int] = None
    session_id: Optional[str] = None
    source: str = SOURCE_UNKNOWN
    # Keys the SDK sent that this projection does not model. Recorded so a
    # reader can see that something arrived unprojected; the VERBATIM payload
    # is in the tick journal, which is the authority.
    unprojected_keys: Tuple[str, ...] = ()
    depth_present: bool = False

    # -- accessors: absent is None, never zero -------------------------- #
    def get(self, name: str) -> FieldValue:
        return self.fields.get(name) or unavailable(self.source)

    def value(self, name: str) -> Optional[float]:
        return self.get(name).value

    @property
    def ltp(self) -> Optional[float]:
        return self.value("ltp")

    @property
    def spread(self) -> Optional[float]:
        """Derived, and only when BOTH sides are real. A spread computed from
        a missing side would be a number with no meaning attached."""
        b, a = self.get("bid"), self.get("ask")
        if b.is_available and a.is_available:
            return float(a.value) - float(b.value)
        return None

    def availability(self) -> Dict[str, str]:
        return {k: v.availability for k, v in self.fields.items()}

    def available_fields(self) -> Tuple[str, ...]:
        return tuple(sorted(k for k, v in self.fields.items() if v.is_available))

    def missing_fields(self) -> Tuple[str, ...]:
        return tuple(sorted(k for k, v in self.fields.items() if not v.is_available))

    def age_seconds(self, now_mono: float) -> Optional[float]:
        if self.recv_mono is None:
            return None
        return max(0.0, now_mono - self.recv_mono)

    def is_fresh(self, now_mono: float, max_age_s: float) -> bool:
        """A quote with no monotonic stamp is NOT fresh. Unknown age is not
        youth, and treating it as such is how a stopped clock prices a
        moving position."""
        age = self.age_seconds(now_mono)
        return age is not None and age <= max_age_s

    def as_dict(self) -> Dict[str, Any]:
        return {"symbol": self.symbol, "source": self.source,
                "sequence": self.sequence, "recv_wall": self.recv_wall,
                "session_id": self.session_id,
                "fields": {k: v.as_dict() for k, v in self.fields.items()},
                "unprojected_keys": list(self.unprojected_keys),
                "depth_present": self.depth_present,
                "field_map_status": FIELD_MAP_STATUS}

    def to_option_leg(self, *, strike: float, option_type: str):
        """Project into the EXISTING analytical model.

        `OptionLeg` already carries exactly these Optionals, so a quote
        becomes a chain leg without inventing a second representation. Greeks
        stay None: they are derived elsewhere and are not in a tick.
        """
        from bujji.market_perception.models import OptionLeg
        return OptionLeg(
            symbol=self.symbol, strike=strike, option_type=option_type,
            ltp=self.value("ltp"), bid=self.value("bid"), ask=self.value("ask"),
            spread=self.spread, volume=self.value("volume"),
            open_interest=self.value("open_interest"),
            iv=None, delta=None, gamma=None, theta=None, vega=None)


def _coerce(v) -> Optional[float]:
    if v is None or isinstance(v, bool):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def project(payload: Mapping[str, Any], *, source: str = SOURCE_TICK,
            recv_wall: Optional[float] = None, recv_mono: Optional[float] = None,
            sequence: Optional[int] = None,
            session_id: Optional[str] = None) -> Quote:
    """Turn one SDK callback into a typed quote.

    LOSSLESS WITH RESPECT TO EVIDENCE. This projection is deliberately partial
    -- it models the fields the runtime can use -- and every key it does not
    model is named in `unprojected_keys`. The verbatim payload is already in
    the tick journal by the time this runs, so a field this misses is
    recoverable from evidence rather than lost.

    A KEY THAT IS ABSENT YIELDS UNAVAILABLE. A key present but null yields
    NULL_REPORTED. Neither yields 0.0, because zero is a measurement.
    """
    fields: Dict[str, FieldValue] = {}
    consumed = set()
    for name, candidates in PROVISIONAL_SDK_FIELD_MAP.items():
        chosen = None
        for key in candidates:
            if key in payload:
                consumed.add(key)
                if payload[key] is not None:
                    chosen = (key, payload[key])
                    break
                chosen = chosen or (key, None)
        if chosen is None:
            fields[name] = unavailable(source)
            continue
        key, raw = chosen
        num = _coerce(raw)
        if raw is None:
            fields[name] = FieldValue(None, NULL_REPORTED, source, recv_wall,
                                      recv_mono, key)
        elif num is None:
            # Present but not a number: malformed, not zero and not available.
            fields[name] = FieldValue(None, NULL_REPORTED, source, recv_wall,
                                      recv_mono, key)
        else:
            fields[name] = FieldValue(num, AVAILABLE, source, recv_wall,
                                      recv_mono, key)

    depth = any(payload.get(k) for k in PROVISIONAL_DEPTH_KEYS)
    consumed.update(k for k in PROVISIONAL_DEPTH_KEYS if k in payload)
    consumed.add("symbol")
    unprojected = tuple(sorted(k for k in payload.keys() if k not in consumed))

    return Quote(symbol=str(payload.get("symbol") or ""), fields=fields,
                 recv_wall=recv_wall, recv_mono=recv_mono, sequence=sequence,
                 session_id=session_id, source=source,
                 unprojected_keys=unprojected, depth_present=depth)


def from_rest_price(symbol: str, price: Optional[float], *,
                    observed_wall: Optional[float] = None,
                    observed_mono: Optional[float] = None) -> Quote:
    """A REST read carries ONE field, and says so.

    Declared as REST_FALLBACK so it can never masquerade as tick evidence.
    Everything else is UNAVAILABLE -- a REST price tells us nothing about the
    book, and filling those in would be fabrication.
    """
    num = _coerce(price)
    fields: Dict[str, FieldValue] = {
        name: unavailable(SOURCE_REST) for name in PROVISIONAL_SDK_FIELD_MAP}
    if num is not None:
        fields["ltp"] = FieldValue(num, AVAILABLE, SOURCE_REST, observed_wall,
                                   observed_mono, "rest:get_ltp")
    return Quote(symbol=symbol, fields=fields, recv_wall=observed_wall,
                 recv_mono=observed_mono, source=SOURCE_REST)
