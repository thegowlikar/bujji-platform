"""Historical Market Reality — Phase 17H.3/17H.4 models.

Implements the `HistoricalObservation` contract from
`docs/PHASE_17H3_HISTORICAL_CERTIFICATION_AND_INGESTION_CONTRACT.md`:
a raw-tier fact ("FYERS told us this existed historically"), structured
identically to Layer 0's `RawObservation` -- a canonical MOC
`Observation` (identity minted by `market_observation.engine.
build_observation()`, the ONLY place that happens, never re-derived
here) plus a lineage wrapper. `HistoricalLineage` is Layer0Lineage's
sibling, not its replacement: separate fields because historical
provenance answers different questions (which raw artifact, which
ingestion run, what request symbol vs. what identity) than live capture
provenance does.

Deliberately does NOT reuse `bujji.market_timeseries.Candle` -- that
model assumes ticks Bujji itself observed (`tick_count`,
`source_observation_ids`, `capture_event_overlap`); a 1998 daily bar
has none of those. See PHASE_17H2's Part 0.1 finding.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional

from bujji.market_observation.models import Observation
from bujji.market_observation.serialization import (
    observation_from_dict,
    observation_to_dict,
)

SCHEMA_VERSION = "1.0.0"


@dataclass(frozen=True)
class HistoricalLineage:
    """Provenance specific to a historically-ingested fact -- distinct
    from Layer0Lineage's live-capture fields (event_timestamp vs.
    capture_timestamp) because historical provenance answers a
    different question: which raw artifact, which ingestion run, and
    -- critically for futures -- what request symbol was used vs. what
    the row's real identity is (PHASE_17H3 Part 2.4)."""

    source: str                              # "fyers_historical"
    access_method: str                       # "direct_sdk_fyers_historical_rest"
    source_epoch: int                        # raw epoch exactly as FYERS returned it
    source_symbol: str                       # the literal request symbol (may differ from instrument identity)
    raw_artifact_ref: str                    # pointer into data/historical_reality/raw_artifacts/
    ingestion_run_id: str
    retrieved_at: str                        # ISO8601+IST -- when Bujji fetched it
    certification_status: str
    certification_ref: Optional[str] = None
    continuity_method: Optional[str] = None  # "fyers_cont_flag_1" for continuous futures; else None
    schema_version: str = SCHEMA_VERSION

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "access_method": self.access_method,
            "source_epoch": self.source_epoch,
            "source_symbol": self.source_symbol,
            "raw_artifact_ref": self.raw_artifact_ref,
            "ingestion_run_id": self.ingestion_run_id,
            "retrieved_at": self.retrieved_at,
            "certification_status": self.certification_status,
            "certification_ref": self.certification_ref,
            "continuity_method": self.continuity_method,
            "schema_version": self.schema_version,
        }

    @staticmethod
    def from_dict(d: Mapping) -> "HistoricalLineage":
        return HistoricalLineage(
            source=d["source"], access_method=d["access_method"],
            source_epoch=d["source_epoch"], source_symbol=d["source_symbol"],
            raw_artifact_ref=d["raw_artifact_ref"], ingestion_run_id=d["ingestion_run_id"],
            retrieved_at=d["retrieved_at"], certification_status=d["certification_status"],
            certification_ref=d.get("certification_ref"),
            continuity_method=d.get("continuity_method"),
            schema_version=d.get("schema_version", SCHEMA_VERSION),
        )


@dataclass(frozen=True)
class HistoricalObservation:
    """One immutable historical fact: a canonical MOC `Observation`
    (identity minted by build_observation(), the same function Layer 0
    uses -- never re-derived here, so a re-ingestion of the identical
    fact always produces the identical id) plus historical-specific
    lineage. `instrument_type` reuses market_reality.taxonomy's
    vocabulary (SPOT | FUTURE | INDEX) verbatim -- no new taxonomy."""

    observation: Observation
    instrument_type: str
    lineage: HistoricalLineage

    @property
    def observation_id(self) -> str:
        return self.observation.identity.observation_id

    @property
    def instrument(self) -> str:
        return self.observation.identity.instrument

    @property
    def payload(self) -> Any:
        return self.observation.value.payload

    def to_dict(self) -> dict:
        return {
            "observation": observation_to_dict(self.observation),
            "instrument_type": self.instrument_type,
            "lineage": self.lineage.to_dict(),
        }

    @staticmethod
    def from_dict(d: Mapping) -> "HistoricalObservation":
        return HistoricalObservation(
            observation=observation_from_dict(d["observation"]),
            instrument_type=d["instrument_type"],
            lineage=HistoricalLineage.from_dict(d["lineage"]),
        )


# --- Ingestion run outcomes (PHASE_17H2 §2.3 / PHASE_17H3 §3.3) -----------
RUN_STATUS_OK = "OK"
RUN_STATUS_NO_DATA = "NO_DATA"
RUN_STATUS_ERROR = "ERROR"
RUN_STATUS_PARTIAL = "PARTIAL"
ALL_RUN_STATUSES = (RUN_STATUS_OK, RUN_STATUS_NO_DATA, RUN_STATUS_ERROR, RUN_STATUS_PARTIAL)


@dataclass(frozen=True)
class IngestionRun:
    """One row per real fetch attempt -- makes `s=no_data` (genuinely
    nothing there) and `s=error` (a rejected request, e.g. >366-day
    range) permanent, distinguishable facts rather than lost console
    output (PHASE_17H2 §2.3)."""

    ingestion_run_id: str
    source: str
    instrument: str
    resolution: str
    range_from: str
    range_to: str
    started_at: str
    status: str
    rows_returned: int
    raw_artifact_path: str
    completed_at: Optional[str] = None
    error_code: Optional[int] = None
    error_message: Optional[str] = None
    rows_accepted: int = 0
    rows_rejected: int = 0

    def to_dict(self) -> dict:
        return {
            "ingestion_run_id": self.ingestion_run_id, "source": self.source,
            "instrument": self.instrument, "resolution": self.resolution,
            "range_from": self.range_from, "range_to": self.range_to,
            "started_at": self.started_at, "completed_at": self.completed_at,
            "status": self.status, "rows_returned": self.rows_returned,
            "rows_accepted": self.rows_accepted, "rows_rejected": self.rows_rejected,
            "error_code": self.error_code, "error_message": self.error_message,
            "raw_artifact_path": self.raw_artifact_path,
        }

    @staticmethod
    def from_dict(d: Mapping) -> "IngestionRun":
        return IngestionRun(
            ingestion_run_id=d["ingestion_run_id"], source=d["source"],
            instrument=d["instrument"], resolution=d["resolution"],
            range_from=d["range_from"], range_to=d["range_to"],
            started_at=d["started_at"], completed_at=d.get("completed_at"),
            status=d["status"], rows_returned=d.get("rows_returned", 0),
            rows_accepted=d.get("rows_accepted", 0), rows_rejected=d.get("rows_rejected", 0),
            error_code=d.get("error_code"), error_message=d.get("error_message"),
            raw_artifact_path=d["raw_artifact_path"],
        )
