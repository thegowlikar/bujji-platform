"""Options Observation Domain models — Engineering Series 73C.

Design decision — wrap, never subclass/mutate (per
`docs/MARKET_OBSERVATION_CONTRACT.md` Section 5, Extension rule 1, and
mirroring `bujji/futures_observation/models.py` exactly):
`OptionObservation` is a thin, frozen wrapper that COMPOSES one
`bujji.market_observation.models.Observation`; it never subclasses
`Observation` and never carries a second copy of any Identity/Value
field. Every options-specific accessor on this class reads through to
the wrapped `Observation` -- there is exactly one source of truth.

Design decision — one Observation per option row, MAPPING-shaped
payload (per Extension rule 2, mirroring 73B): an option row carries
more than an OHLC bundle -- settlement, volume, open interest, change
in open interest, underlying price, and the (always-None-from-Bhavcopy
but schema-present) bid/ask/bid-qty/ask-qty -- so
`taxonomy.VALUE_KIND_MAPPING` is used, with `payload` a
`Mapping[str, Optional[float]]` keyed by
`taxonomy.ALL_OPTIONS_OBSERVATION_FIELDS`.

Design decision — identity vs. value split (the single most important
design decision in this module; see also
`bujji/market_observation/models.py`'s own module docstring on
`observation_id` and quality metadata):

  IDENTITY-shaping (threaded through MOC's `ObservationIdentity` +
  this wrapper's own explicit fields, and therefore hashed into
  `observation_id`): `strike`, `expiry`, `option_type`, `underlying`,
  `timestamp`, `instrument` (the contract-specific symbol, e.g.
  "ABCAPITAL26JUL360PE"). An option CONTRACT is defined by
  underlying+strike+expiry+option_type -- these fields identify WHICH
  fact is being recorded, not what its value was. `strike` and
  `option_type` are options-domain identity concepts, exactly parallel
  to how 73B threads `expiry`/`underlying` alongside MOC's generic
  `ObservationIdentity` (which has no native notion of strike/expiry/
  option_type/underlying-vs-contract).

  VALUE-shaping (carried inside `ObservationValue.payload`, and
  therefore ALSO hashed into `observation_id`, per MOC's own
  `engine.build_observation`, which hashes identity fields + value
  together): OPEN, HIGH, LOW, CLOSE, SETTLEMENT, VOLUME,
  OPEN_INTEREST, CHANGE_IN_OPEN_INTEREST, UNDERLYING_PRICE, BID, ASK,
  BID_QUANTITY, ASK_QUANTITY. These are the observed FACTS about the
  already-identified contract at the already-identified timestamp --
  what was traded/settled/held, not which contract this is.

  Consequence, deliberately accepted (consistent with MOC's own design
  note in `market_observation/models.py`): two Observations of the
  SAME contract (same underlying+strike+expiry+option_type) at the
  SAME timestamp but with DIFFERENT values (e.g. a revised/corrected
  Bhavcopy re-publish) get DIFFERENT `observation_id`s, because value
  is part of the content hash. This is intentional, not an oversight:
  MOC's own docstring establishes that quality metadata never affects
  identity, but a genuine value difference is a genuine different fact
  and should not silently collide under one id. The correction is
  tracked as a new version of the same underlying contract-fact via
  `ObservationProvenance.version` (MOC's `ObservationVersion`
  provenance concept), not via identity collision/overwrite -- exactly
  mirroring how 73B resolves the identical question for futures
  observations (73B's design intentionally leaves this question to
  MOC's existing versioning primitive rather than re-solving it here).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Optional, Tuple

from bujji.market_observation.models import Observation, ObservationSeries

from . import taxonomy


@dataclass(frozen=True)
class OptionObservation:
    """A single option Bhavcopy row, recorded as one MOC `Observation`
    plus options-specific typed accessors. Composition only -- no
    second source of truth. `strike`, `expiry`, `option_type`, and
    `underlying` are options-domain identity concepts not present in
    MOC's generic `ObservationIdentity`, so they are threaded through
    explicitly at construction time (engine.py) and stored alongside
    the wrapped Observation, never re-derived by string-parsing
    `instrument`.
    """

    observation: Observation
    strike: float
    expiry: str
    option_type: str
    underlying: str
    # WHERE instrument_symbol CAME FROM (taxonomy.ALL_SYMBOL_PROVENANCES).
    #
    # DELIBERATELY ON THE WRAPPER, NOT INSIDE `observation`. `observation_id`
    # is computed by moc_engine.build_observation() from identity + value
    # ALONE, and this field is attached only after that call returns. Its
    # non-participation in content-addressing is therefore STRUCTURAL -- not
    # a convention a later edit could quietly break -- which matters because
    # every historical option row in the observation store is addressed by
    # that hash.
    symbol_provenance: str

    @property
    def observation_id(self) -> str:
        return self.observation.identity.observation_id

    @property
    def instrument_symbol(self) -> str:
        """The contract-specific symbol (e.g. "ABCAPITAL26JUL360PE"),
        read from MOC's own `ObservationIdentity.instrument` -- mirrors
        73B's `FuturesObservation.instrument_symbol` convention
        exactly."""
        return self.observation.identity.instrument

    @property
    def timestamp(self) -> str:
        return self.observation.identity.timestamp

    @property
    def resolution(self) -> str:
        return self.observation.identity.resolution

    @property
    def observation_type(self) -> str:
        return self.observation.identity.observation_type

    @property
    def payload(self) -> Mapping[str, Optional[float]]:
        return self.observation.value.payload

    def field(self, field_name: str) -> Optional[float]:
        """Read one field (taxonomy.ALL_OPTIONS_OBSERVATION_FIELDS) from
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
    def settlement(self) -> Optional[float]:
        return self.field(taxonomy.FIELD_SETTLEMENT)

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
    def previous_open_interest(self) -> Optional[float]:
        """The broker's own previous-session OI, verbatim.

        None means UNAVAILABLE, never zero. A real reported 0 and an absent
        field are opposite facts: one says the contract has no open interest,
        the other says nobody told us. Callers that cannot tell them apart
        will eventually compare a fabricated zero to a threshold.
        """
        return self.field(taxonomy.FIELD_PREVIOUS_OPEN_INTEREST)

    @property
    def underlying_price(self) -> Optional[float]:
        return self.field(taxonomy.FIELD_UNDERLYING_PRICE)

    @property
    def bid(self) -> Optional[float]:
        """Always None for Bhavcopy-sourced data -- see taxonomy.py's
        module docstring for the disclosed evidence. The field exists
        in the schema for a future live/L2 source; never fabricated."""
        return self.field(taxonomy.FIELD_BID)

    @property
    def ask(self) -> Optional[float]:
        """Always None for Bhavcopy-sourced data -- see taxonomy.py."""
        return self.field(taxonomy.FIELD_ASK)

    @property
    def bid_quantity(self) -> Optional[float]:
        """Always None for Bhavcopy-sourced data -- see taxonomy.py."""
        return self.field(taxonomy.FIELD_BID_QUANTITY)

    @property
    def ask_quantity(self) -> Optional[float]:
        """Always None for Bhavcopy-sourced data -- see taxonomy.py."""
        return self.field(taxonomy.FIELD_ASK_QUANTITY)

    @property
    def missing_fields(self) -> Tuple[str, ...]:
        return self.observation.quality.missing_fields


@dataclass(frozen=True)
class OptionObservationSeries:
    """Thin wrapper over one
    `bujji.market_observation.models.ObservationSeries`, for one
    (underlying, strike, expiry, option_type) contract at a defined
    resolution, plus options-typed accessors over its Observations.
    Composition only, mirroring `FuturesObservationSeries` -- never a
    parallel storage structure. One series per option contract, exactly
    parallel to 73B's one-series-per-futures-contract precedent (an
    option contract is the options-market equivalent of a futures
    contract).
    """

    series: ObservationSeries
    strike: float
    expiry: str
    option_type: str
    underlying: str
    # Same provenance, carried at series level for one concrete reason:
    # observations() and query.py rebuild OptionObservation wrappers from
    # the SERIES. Without it here those rebuilds would have nothing honest
    # to declare and would have to invent a value -- exactly the failure
    # this field exists to prevent. engine.append_option_observation()
    # refuses to mix provenances within one series.
    symbol_provenance: str

    @property
    def instrument_symbol(self) -> str:
        return self.series.instrument

    @property
    def resolution(self) -> str:
        return self.series.resolution

    def observations(self) -> Tuple[OptionObservation, ...]:
        return tuple(
            OptionObservation(
                observation=o,
                strike=self.strike,
                expiry=self.expiry,
                option_type=self.option_type,
                underlying=self.underlying,
                symbol_provenance=self.symbol_provenance,
            )
            for o in self.series.observations
        )

    def __len__(self) -> int:
        return len(self.series.observations)
