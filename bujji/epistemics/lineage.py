"""Canonical lineage contract -- Phase 16C.

Pure model. No IO, no storage, no market data. Nothing here depends on
Gate 1 measurements.

AUDITED POSITION (verified, not assumed):
  * `provenance` appears in 171 files -- but as a FIELD on individual
    models, never as a shared contract. `schema_version` likewise (171).
  * `market_observation.ObservationProvenance` is the closest existing
    shape: (originating_source, acquisition_timestamp,
    normalization_timestamp, origin, version, transformation_history).
  * `calc_version`, `feature_version`, `state_version`: ZERO files.
  * `as_of` is absent from every store read signature.

So the concept is pervasive and the CONTRACT is missing. This module
supplies the contract WITHOUT competing with `ObservationProvenance`:
`from_observation_provenance()` adapts it, and no existing model is
rewritten.

THE EIGHT QUESTIONS every derived artifact must answer:
  1. What source observations produced this?   -> source_event_ids
  2. As of what point in time?                 -> as_of  (EVENT time)
  3. Which calculation version?                -> calc_version
  4. Which feature version?                    -> feature_version
  5. Which state version?                      -> state_version
  6. Which code version?                       -> code_version
  7. Which configuration version?               -> config_version
  8. Which decision/event produced it?          -> origin_event_id
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Mapping, Optional, Tuple

SCHEMA_VERSION = "1.0.0"

# --- Data class (authoritative vs derived) -----------------------------------
AUTHORITATIVE = "AUTHORITATIVE"   # cannot be recomputed; capture or lose forever
DERIVED = "DERIVED"               # reproducible given inputs + versions
EPHEMERAL = "EPHEMERAL"           # never persisted; always reconstructable
ALL_DATA_CLASSES = (AUTHORITATIVE, DERIVED, EPHEMERAL)


def calc_version_for(definition_source: str, parameters: Mapping = ()) -> str:
    """A CONTENT HASH of the calculation, not a hand-maintained integer.

    Hand-maintained versions drift the moment someone edits a formula
    and forgets to bump -- and a silently-drifted version is worse than
    none, because it makes two incompatible definitions look like one
    series. Deriving it from the definition makes that impossible.
    """
    payload = definition_source + "|" + repr(sorted(dict(parameters).items()))
    return "CV-" + hashlib.sha256(payload.encode()).hexdigest()[:12]


@dataclass(frozen=True)
class Lineage:
    """Travels WITH every derived artifact.

    `as_of` is EVENT time (exchange time), never arrival time -- a
    lineage stamped with arrival time cannot prove absence of
    look-ahead, which is the main thing lineage exists to prove.
    """

    data_class: str                                  # one of ALL_DATA_CLASSES
    as_of: str                                       # event-time instant this was computed "as if"
    calc_version: str
    code_version: Optional[str] = None
    config_version: Optional[str] = None
    feature_version: Optional[str] = None
    state_version: Optional[str] = None
    source_event_ids: Tuple[str, ...] = ()
    origin_event_id: Optional[str] = None            # the decision/event that produced this
    instrument_id: Optional[str] = None
    timeframe: Optional[str] = None
    event_time_start: Optional[str] = None
    event_time_end: Optional[str] = None
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.data_class not in ALL_DATA_CLASSES:
            raise ValueError(f"unknown data_class {self.data_class!r}")
        if self.data_class == DERIVED and not self.calc_version:
            raise ValueError("a DERIVED artifact requires a calc_version")

    @property
    def is_reproducible(self) -> bool:
        """True only when every version needed to recompute is present.
        A DERIVED artifact that is not reproducible is, in practice,
        untrustworthy evidence."""
        if self.data_class == AUTHORITATIVE:
            return True
        return all([self.calc_version, self.code_version, self.config_version])

    def missing_fields(self) -> Tuple[str, ...]:
        """What this artifact cannot answer. Named explicitly rather
        than silently absent."""
        missing = []
        if self.data_class == DERIVED:
            for f in ("calc_version", "code_version", "config_version"):
                if not getattr(self, f):
                    missing.append(f)
            if not self.source_event_ids:
                missing.append("source_event_ids")
        return tuple(missing)

    def descends_from(self, *parents: "Lineage") -> "Lineage":
        """Build a child lineage that inherits every parent's source
        events. Source events accumulate; they are never summarised
        away, so a chain remains traceable to its authoritative roots."""
        inherited: list = list(self.source_event_ids)
        for p in parents:
            for eid in p.source_event_ids:
                if eid not in inherited:
                    inherited.append(eid)
        return Lineage(
            data_class=self.data_class, as_of=self.as_of, calc_version=self.calc_version,
            code_version=self.code_version, config_version=self.config_version,
            feature_version=self.feature_version, state_version=self.state_version,
            source_event_ids=tuple(inherited), origin_event_id=self.origin_event_id,
            instrument_id=self.instrument_id, timeframe=self.timeframe,
            event_time_start=self.event_time_start, event_time_end=self.event_time_end,
        )

    def to_dict(self) -> dict:
        return {
            "data_class": self.data_class, "as_of": self.as_of,
            "calc_version": self.calc_version, "code_version": self.code_version,
            "config_version": self.config_version, "feature_version": self.feature_version,
            "state_version": self.state_version, "source_event_ids": list(self.source_event_ids),
            "origin_event_id": self.origin_event_id, "instrument_id": self.instrument_id,
            "timeframe": self.timeframe, "event_time_start": self.event_time_start,
            "event_time_end": self.event_time_end, "schema_version": self.schema_version,
        }

    @staticmethod
    def from_dict(d: Mapping) -> "Lineage":
        return Lineage(
            data_class=d["data_class"], as_of=d["as_of"], calc_version=d.get("calc_version", ""),
            code_version=d.get("code_version"), config_version=d.get("config_version"),
            feature_version=d.get("feature_version"), state_version=d.get("state_version"),
            source_event_ids=tuple(d.get("source_event_ids") or ()),
            origin_event_id=d.get("origin_event_id"), instrument_id=d.get("instrument_id"),
            timeframe=d.get("timeframe"), event_time_start=d.get("event_time_start"),
            event_time_end=d.get("event_time_end"),
            schema_version=d.get("schema_version", SCHEMA_VERSION),
        )

    @staticmethod
    def from_observation_provenance(prov, *, as_of: str, data_class: str = AUTHORITATIVE,
                                     event_id: Optional[str] = None) -> "Lineage":
        """ADAPTER for `market_observation.ObservationProvenance` --
        the existing canonical shape (171 files use a `provenance`
        field). Adapts, never replaces: no existing model changes."""
        return Lineage(
            data_class=data_class, as_of=as_of,
            calc_version=getattr(prov, "version", "") or "",
            code_version=getattr(prov, "version", None),
            source_event_ids=(event_id,) if event_id else (),
            origin_event_id=getattr(prov, "originating_source", None),
        )


def look_ahead_violation(lineage: Lineage, source_event_time: str) -> bool:
    """True when a source event is LATER than the artifact's `as_of` --
    i.e. the calculation used information from its own future.

    String comparison is valid for ISO-8601 timestamps in a single
    timezone, which is this codebase's convention (IST throughout, see
    core/clock.py). Callers mixing offsets must normalise first.
    """
    return source_event_time > lineage.as_of
