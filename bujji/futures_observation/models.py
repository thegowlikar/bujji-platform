"""Futures Observation Domain models — Engineering Series 73B.

Design decision — wrap, never subclass/mutate (per
`docs/MARKET_OBSERVATION_CONTRACT.md` Section 5, Extension rule 1):
`FuturesObservation` is a thin, frozen wrapper that COMPOSES one
`bujji.market_observation.models.Observation`, it never subclasses
`Observation` and never carries a second copy of any Identity/Value
field. Every futures-specific accessor on this class reads through to
the wrapped `Observation` -- there is exactly one source of truth.

Design decision — one Observation per futures row, MAPPING-shaped
payload (per Extension rule 2: never introduce a per-domain
Identity/Value type; if the payload doesn't fit
SCALAR/OHLC/MAPPING/TEXT, add one more *value_kind*, never a subclass).
A futures row carries more than an OHLC bundle -- volume, open
interest, change in open interest, settlement price, and an optional
derived basis -- so `taxonomy.VALUE_KIND_MAPPING` (already part of
MOC's closed `ALL_VALUE_KINDS`) is used, with `payload` a
`Mapping[str, Optional[float]]` keyed by
`taxonomy.ALL_FUTURES_OBSERVATION_FIELDS`. This avoids inventing a new
`value_kind` and avoids splitting one futures row into eight separate
scalar Observations (which would multiply observation_ids for what is,
on the wire, a single recorded fact).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Optional, Tuple

from bujji.market_observation.models import Observation, ObservationSeries

from . import taxonomy


@dataclass(frozen=True)
class FuturesObservation:
    """A single futures Bhavcopy row, recorded as one MOC `Observation`
    plus futures-specific typed accessors. Composition only -- no
    second source of truth. `expiry` and `instrument_symbol` are
    futures-domain concepts not present in MOC's generic
    `ObservationIdentity`, so they are threaded through explicitly at
    construction time (engine.py) and stored alongside the wrapped
    Observation, never re-derived by string-parsing `instrument`.
    """

    observation: Observation
    expiry: str
    underlying: str

    @property
    def observation_id(self) -> str:
        return self.observation.identity.observation_id

    @property
    def instrument_symbol(self) -> str:
        """The contract-specific symbol (e.g. "ABCAPITAL26MAYFUT"),
        read from MOC's own `ObservationIdentity.instrument` -- per
        `docs/MARKET_OBSERVATION_CONTRACT.md`'s own illustrative usage,
        `instrument` is the contract-specific string, not the bare
        underlying. `underlying` (e.g. "ABCAPITAL") is a
        futures-domain concept threaded through separately, since MOC
        has no notion of "underlying vs. contract"."""
        return self.observation.identity.instrument

    @property
    def timestamp(self) -> str:
        return self.observation.identity.timestamp

    @property
    def resolution(self) -> str:
        return self.observation.identity.resolution

    @property
    def payload(self) -> Mapping[str, Optional[float]]:
        return self.observation.value.payload

    def field(self, field_name: str) -> Optional[float]:
        """Read one field (taxonomy.ALL_FUTURES_OBSERVATION_FIELDS) from
        the wrapped Observation's payload. Returns None both when the
        field key is absent and when it was recorded as an honest gap
        -- callers that must distinguish "never had this field" from
        "field present but not observed on this row" should instead
        inspect `self.observation.quality.missing_fields`.
        """
        return self.payload.get(field_name)

    @property
    def open(self) -> Optional[float]:
        return self.field(taxonomy.FIELD_OPEN)

    @property
    def high(self) -> Optional[float]:
        return self.field(taxonomy.FIELD_HIGH)

    @property
    def low(self) -> Optional[float]:
        return self.field(taxonomy.FIELD_LOW)

    @property
    def close(self) -> Optional[float]:
        return self.field(taxonomy.FIELD_CLOSE)

    @property
    def volume(self) -> Optional[float]:
        return self.field(taxonomy.FIELD_VOLUME)

    @property
    def open_interest(self) -> Optional[float]:
        return self.field(taxonomy.FIELD_OPEN_INTEREST)

    @property
    def change_in_open_interest(self) -> Optional[float]:
        return self.field(taxonomy.FIELD_CHANGE_IN_OPEN_INTEREST)

    @property
    def settlement_price(self) -> Optional[float]:
        return self.field(taxonomy.FIELD_SETTLEMENT_PRICE)

    @property
    def basis(self) -> Optional[float]:
        return self.field(taxonomy.FIELD_BASIS)

    @property
    def missing_fields(self) -> Tuple[str, ...]:
        return self.observation.quality.missing_fields


@dataclass(frozen=True)
class FuturesObservationSeries:
    """Thin wrapper over one
    `bujji.market_observation.models.ObservationSeries`, for one
    instrument+expiry+resolution, plus futures-typed accessors over its
    Observations. Composition only, mirroring `FuturesObservation`
    above -- never a parallel storage structure.
    """

    series: ObservationSeries
    expiry: str
    underlying: str

    @property
    def instrument_symbol(self) -> str:
        return self.series.instrument

    @property
    def resolution(self) -> str:
        return self.series.resolution

    def observations(self) -> Tuple[FuturesObservation, ...]:
        return tuple(
            FuturesObservation(
                observation=o,
                expiry=self.expiry,
                underlying=self.underlying,
            )
            for o in self.series.observations
        )

    def __len__(self) -> int:
        return len(self.series.observations)
