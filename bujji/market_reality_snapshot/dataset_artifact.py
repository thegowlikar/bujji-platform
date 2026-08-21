"""Dataset Artifact Registry — Phase 18.12.

Closes PHASE_18_11's own audit finding: `DatasetVersion` (Phase 18.7)
is a pure in-memory computation -- correct and deterministic, but
never kept. This module adds exactly the two concepts that audit named
and nothing else:

  * `DatasetIdentity` -- CONTENT identity. Deterministic: the same
    underlying Reality facts always produce the same `dataset_id`.
    Never stored on its own; always derived, on demand, from a real
    `DatasetVersion` via `derive_dataset_identity()`.
  * `DatasetArtifact` -- EVENT identity. Assigned ONCE, at creation,
    persisted, and NEVER regenerated -- two artifacts built from
    identical content at different times correctly share one
    `dataset_id` while holding two distinct `artifact_id`s (PHASE_18_11
    §2's own worked distinction).

Reuses, does not duplicate: `dataset_version.build_dataset_version()`
(Phase 18.7/18.10, unmodified), `replay_engine.fingerprint_state()`
(Phase 18.3's own precedent, reused a third time), and
`epistemics.identity.resolve_code_identity()` (Phase 16D, wired in for
the first time here -- PHASE_18_11's own gap #6).

Raw Reality (`HistoricalObservationStore`), `MarketRealitySnapshot`,
and `DatasetVersion` itself are read-only inputs here -- this module
writes ONLY to its own new `DatasetArtifactStore`, never to any
existing store.
"""
from __future__ import annotations

import datetime
import platform
import uuid
from dataclasses import dataclass
from typing import Mapping, Optional, Tuple

from bujji.epistemics.identity import resolve_code_identity
from bujji.historical_reality.store import HistoricalObservationStore
from bujji.market_reality import taxonomy as reality_taxonomy
from bujji.market_reality.certification import CertificationGate
from bujji.replay_engine.engine import fingerprint_state

from .builder import build_market_reality_snapshot
from .dataset_artifact_store import DatasetArtifactStore
from .dataset_lifecycle_store import DatasetLifecycleStore
from .dataset_version import DatasetVersion, build_dataset_version
from .models import RESOLUTION_DAILY, RESOLUTION_FIVE_MINUTE, SCHEMA_VERSION

IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
CURRENT_SCHEMA_VERSION = SCHEMA_VERSION  # reused verbatim -- no second schema-version constant.

# The four canonical Bujji instruments and their REAL access_method
# constants, per resolution -- read directly from each ingestion
# script's own `ACCESS_METHOD` constant (PHASE_18_14 audit), not
# guessed. Reused here, unchanged, to drive real `CertificationGate`
# lookups -- no new certification vocabulary is invented.
_ACCESS_METHOD_DAILY = "direct_sdk_fyers_historical_rest"
_ACCESS_METHOD_INTRADAY = "direct_sdk_fyers_historical_intraday_rest"
_ACCESS_METHOD_OPTIONS = "direct_sdk_fyers_optionchain_reality"

_INSTRUMENT_TYPE_BY_COMPONENT = {
    "spot": reality_taxonomy.INSTRUMENT_SPOT,
    "futures": reality_taxonomy.INSTRUMENT_FUTURE,
    "vix": reality_taxonomy.INSTRUMENT_INDEX,
    "options": reality_taxonomy.INSTRUMENT_OPTION,
}

DEFAULT_REQUIRED_INSTRUMENTS = ("spot", "futures", "vix", "options")

# --- PHASE_18_14: lifecycle states -------------------------------------------
STATE_CREATED = "CREATED"
STATE_VALIDATING = "VALIDATING"
STATE_CERTIFIED = "CERTIFIED"
STATE_PUBLISHED = "PUBLISHED"
STATE_DEPRECATED = "DEPRECATED"
STATE_INVALID = "INVALID"
ALL_LIFECYCLE_STATES = (STATE_CREATED, STATE_VALIDATING, STATE_CERTIFIED,
                         STATE_PUBLISHED, STATE_DEPRECATED, STATE_INVALID)

# Explicit, closed transition table -- anything not listed here is
# refused (PHASE_18_14's own "invalid transitions must fail"
# requirement). DEPRECATED/INVALID are terminal: once reached, no
# further transition is permitted, matching "a published artifact must
# never silently change" extended to its own lifecycle record.
ALLOWED_LIFECYCLE_TRANSITIONS = {
    STATE_CREATED: (STATE_VALIDATING, STATE_INVALID),
    STATE_VALIDATING: (STATE_CERTIFIED, STATE_INVALID),
    STATE_CERTIFIED: (STATE_PUBLISHED, STATE_DEPRECATED, STATE_INVALID),
    STATE_PUBLISHED: (STATE_DEPRECATED,),
    STATE_DEPRECATED: (),
    STATE_INVALID: (),
}

# Only these states permit research/backtest use -- consulted by
# `check_backtest_eligibility()` below.
LIFECYCLE_STATES_ALLOWING_RESEARCH_USE = (STATE_CERTIFIED, STATE_PUBLISHED)


class InvalidLifecycleTransitionError(Exception):
    """Raised, never silently ignored or coerced, when a requested
    lifecycle transition is not in `ALLOWED_LIFECYCLE_TRANSITIONS`."""


def _access_method_for(component: str, resolution: str) -> Optional[str]:
    if component == "options":
        return _ACCESS_METHOD_OPTIONS
    if component in ("spot", "futures", "vix"):
        return _ACCESS_METHOD_DAILY if resolution == RESOLUTION_DAILY else _ACCESS_METHOD_INTRADAY
    return None


@dataclass(frozen=True)
class DatasetIdentity:
    """Pure content identity -- a function of Reality facts and
    reconstruction logic only, never of when or by whom it was
    computed. `fingerprint` and `dataset_id` are deliberately two
    separate hashes, not one collapsed into the other:

    `fingerprint` = a pure aggregate of the per-date SNAPSHOT
    fingerprints alone (`DatasetVersion.fingerprint_lineage`) -- "did
    the underlying market facts change."

    `dataset_id` = the FULL content identity: date range, resolution,
    instrument universe, `reconstruction_version`, `schema_version`,
    AND `fingerprint` folded together -- "is this the same dataset in
    every respect a customer could mean by that phrase," closing
    PHASE_18_11 §2's own gap (schema_version was never rolled up to
    the dataset level before this phase)."""

    dataset_id: str
    fingerprint: str
    date_range: Tuple[str, str]
    resolution: str
    instruments: Tuple[str, ...]
    reconstruction_version: str
    schema_version: str

    def to_dict(self) -> dict:
        return {
            "dataset_id": self.dataset_id, "fingerprint": self.fingerprint,
            "date_range": list(self.date_range), "resolution": self.resolution,
            "instruments": list(self.instruments),
            "reconstruction_version": self.reconstruction_version,
            "schema_version": self.schema_version,
        }

    @staticmethod
    def from_dict(d: Mapping) -> "DatasetIdentity":
        return DatasetIdentity(
            dataset_id=d["dataset_id"], fingerprint=d["fingerprint"],
            date_range=tuple(d["date_range"]), resolution=d["resolution"],
            instruments=tuple(d["instruments"]),
            reconstruction_version=d["reconstruction_version"], schema_version=d["schema_version"],
        )


def derive_dataset_identity(dataset_version: DatasetVersion,
                             schema_version: str = CURRENT_SCHEMA_VERSION) -> DatasetIdentity:
    """The ONLY place a `DatasetIdentity` is computed -- mirrors
    `market_observation.engine.build_observation()`'s own "one place
    mints identity" discipline. Reruns of this function against
    unchanged inputs always produce the identical `dataset_id`
    (PHASE_18_12's own reproducibility demonstration proves this
    live, not just by construction)."""
    fp = fingerprint_state({"fingerprint_lineage": list(dataset_version.fingerprint_lineage)})
    dataset_id = fingerprint_state({
        "date_range": [dataset_version.coverage_start, dataset_version.coverage_end],
        "resolution": dataset_version.resolution,
        "instruments": list(dataset_version.included_components),
        "reconstruction_version": dataset_version.reconstruction_version,
        "schema_version": schema_version,
        "fingerprint": fp,
    })
    return DatasetIdentity(
        dataset_id=dataset_id, fingerprint=fp,
        date_range=(dataset_version.coverage_start, dataset_version.coverage_end),
        resolution=dataset_version.resolution, instruments=dataset_version.included_components,
        reconstruction_version=dataset_version.reconstruction_version, schema_version=schema_version,
    )


@dataclass(frozen=True)
class DatasetArtifact:
    """One frozen creation event. `artifact_id` is minted once
    (`uuid.uuid4()`, real, non-deterministic by design -- an EVENT
    identity is correctly allowed to differ between two artifacts that
    share the same `dataset_id`) and never recomputed; the object
    itself is immutable (`frozen=True`) and the store it is written
    to (`DatasetArtifactStore`) refuses to silently overwrite one
    (PHASE_18_11's own "artifact identity must NOT be regenerated"
    requirement, enforced at both the model and storage layers)."""

    artifact_id: str
    dataset_id: str
    created_at: str
    created_by: str
    fingerprint: str
    dataset_version_reference: str          # DatasetVersion.dataset_version_id (Phase 18.7, unmodified concept).
    certification_references: Tuple[str, ...]
    ingestion_run_references: Tuple[str, ...]
    code_identity: dict                      # resolve_code_identity().to_dict() -- honest, incl. `dirty`.
    environment_identity: dict
    dirty_state: Optional[bool]
    # Enough to REBUILD deterministically for verify_artifact() -- an
    # artifact that could not be independently re-derived would not be
    # a reproducibility proof, only a claim.
    date_range: Tuple[str, str]
    resolution: str
    as_of_time_of_day: Optional[str]
    reconstruction_version: str
    schema_version: str
    # PHASE_18_14, additive: optional pointer to the artifact this one
    # EXTENDS (same research line, more data/a later creation) --
    # caller-supplied, never inferred or auto-matched by content. `None`
    # for a first-of-its-line artifact. Does NOT mutate the parent in
    # any way (PHASE_18_13's own "never mutate A" requirement) -- this
    # is purely an additional pointer on the CHILD record.
    parent_artifact_id: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "artifact_id": self.artifact_id, "dataset_id": self.dataset_id,
            "created_at": self.created_at, "created_by": self.created_by,
            "fingerprint": self.fingerprint,
            "dataset_version_reference": self.dataset_version_reference,
            "certification_references": list(self.certification_references),
            "ingestion_run_references": list(self.ingestion_run_references),
            "code_identity": self.code_identity, "environment_identity": self.environment_identity,
            "dirty_state": self.dirty_state,
            "date_range": list(self.date_range), "resolution": self.resolution,
            "as_of_time_of_day": self.as_of_time_of_day,
            "reconstruction_version": self.reconstruction_version, "schema_version": self.schema_version,
            "parent_artifact_id": self.parent_artifact_id,
        }

    @staticmethod
    def from_dict(d: Mapping) -> "DatasetArtifact":
        return DatasetArtifact(
            artifact_id=d["artifact_id"], dataset_id=d["dataset_id"],
            created_at=d["created_at"], created_by=d["created_by"], fingerprint=d["fingerprint"],
            dataset_version_reference=d["dataset_version_reference"],
            certification_references=tuple(d.get("certification_references") or ()),
            ingestion_run_references=tuple(d.get("ingestion_run_references") or ()),
            code_identity=dict(d["code_identity"]), environment_identity=dict(d["environment_identity"]),
            dirty_state=d.get("dirty_state"),
            date_range=tuple(d["date_range"]), resolution=d["resolution"],
            as_of_time_of_day=d.get("as_of_time_of_day"),
            reconstruction_version=d["reconstruction_version"], schema_version=d["schema_version"],
            parent_artifact_id=d.get("parent_artifact_id"),
        )


def _resolve_environment_identity() -> dict:
    """Real, standard-library facts only -- never fabricated, never
    guessed. Deliberately separate from `code_identity` (which answers
    "which commit"): this answers "which machine/interpreter," a
    different axis of reproducibility PHASE_18_11 §2 named but
    `resolve_code_identity()` alone does not cover."""
    return {
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "processor": platform.processor() or None,
    }


def _collect_certification_refs(
    start_date: str, end_date: str, *,
    historical_store: HistoricalObservationStore, resolution: str, as_of_time_of_day: Optional[str],
) -> Tuple[str, ...]:
    """Independent pass over the same date range `build_dataset_version()`
    already covers -- NOT read off `DatasetVersion` (which does not
    carry certification refs, PHASE_18_11 §2's own confirmed gap) and
    NOT achieved by modifying `MarketRealitySnapshot`/`ResearchSessionReadiness`
    (this phase's own regression requirement: both stay byte-for-byte
    unchanged). Re-derives each date's snapshot directly and reads its
    already-existing `certification_refs` field -- a real, if
    duplicated, read; disclosed in the PHASE_18_12 report as a known,
    accepted cost rather than hidden."""
    start = datetime.date.fromisoformat(start_date)
    end = datetime.date.fromisoformat(end_date)
    refs = set()
    current = start
    while current <= end:
        date_str = current.isoformat()
        as_of = f"{date_str}T{as_of_time_of_day}" if as_of_time_of_day else None
        snap = build_market_reality_snapshot(
            date_str, historical_store=historical_store, resolution=resolution, as_of_time=as_of,
        )
        refs.update(snap.certification_refs)
        current += datetime.timedelta(days=1)
    return tuple(sorted(refs))


def create_artifact(
    start_date: str, end_date: str, *,
    historical_store: HistoricalObservationStore, artifact_store: DatasetArtifactStore,
    resolution: str = RESOLUTION_DAILY, as_of_time_of_day: Optional[str] = None,
    created_by: str = "unknown", now: Optional[datetime.datetime] = None,
    repo_root: Optional[str] = None, parent_artifact_id: Optional[str] = None,
    lifecycle_store: Optional[DatasetLifecycleStore] = None,
) -> DatasetArtifact:
    """Builds a real `DatasetVersion` (Phase 18.7/18.10, unmodified),
    derives its `DatasetIdentity`, resolves real code/environment
    identity, and persists exactly one immutable artifact record.
    Never raises for missing/incomplete data (matches every other
    builder in this module family) -- an artifact can honestly
    represent a PARTIAL or even EMPTY dataset; `DatasetVersion.ready_dates`/
    `incomplete_dates` already say which, unchanged. Insufficiency is
    surfaced later, explicitly, by `run_certification_checks()`/
    `check_backtest_eligibility()` -- creation itself stays a pure,
    honest record of what was asked for and what was found, per this
    project's own "never raise on absence" discipline.

    `parent_artifact_id` (PHASE_18_14): purely a caller-supplied
    pointer, never auto-matched by content -- this function does not
    attempt to guess which prior artifact, if any, this one "extends."

    `lifecycle_store` (PHASE_18_14): if supplied, an initial `CREATED`
    lifecycle event is recorded for the new artifact -- optional so
    every pre-18.14 caller (and every existing test) that never passes
    one continues to work unchanged."""
    dataset_version = build_dataset_version(
        start_date, end_date, historical_store=historical_store,
        resolution=resolution, as_of_time_of_day=as_of_time_of_day, now=now,
    )
    identity = derive_dataset_identity(dataset_version, schema_version=CURRENT_SCHEMA_VERSION)
    certification_references = _collect_certification_refs(
        start_date, end_date, historical_store=historical_store,
        resolution=resolution, as_of_time_of_day=as_of_time_of_day,
    )
    code_identity = resolve_code_identity(repo_root).to_dict()
    environment_identity = _resolve_environment_identity()
    created_at = (now or datetime.datetime.now(IST)).isoformat()

    artifact = DatasetArtifact(
        artifact_id="ART-" + uuid.uuid4().hex,
        dataset_id=identity.dataset_id, created_at=created_at, created_by=created_by,
        fingerprint=identity.fingerprint, dataset_version_reference=dataset_version.dataset_version_id,
        certification_references=certification_references,
        ingestion_run_references=dataset_version.ingestion_run_references,
        code_identity=code_identity, environment_identity=environment_identity,
        dirty_state=code_identity.get("dirty"),
        date_range=(start_date, end_date), resolution=resolution, as_of_time_of_day=as_of_time_of_day,
        reconstruction_version=dataset_version.reconstruction_version, schema_version=CURRENT_SCHEMA_VERSION,
        parent_artifact_id=parent_artifact_id,
    )
    artifact_store.write(artifact.artifact_id, artifact.dataset_id, artifact.created_at, artifact.to_dict())
    if lifecycle_store is not None:
        lifecycle_store.append(artifact.artifact_id, created_at, {
            "from_state": None, "to_state": STATE_CREATED, "occurred_at": created_at, "reason": "artifact created",
        })
    return artifact


def get_artifact(artifact_id: str, *, artifact_store: DatasetArtifactStore) -> Optional[DatasetArtifact]:
    record = artifact_store.get(artifact_id)
    return DatasetArtifact.from_dict(record) if record is not None else None


def list_artifacts(
    *, artifact_store: DatasetArtifactStore, dataset_id: Optional[str] = None, limit: Optional[int] = None,
) -> Tuple[DatasetArtifact, ...]:
    records = (artifact_store.list_for_dataset(dataset_id) if dataset_id is not None
               else artifact_store.list_all(limit=limit))
    return tuple(DatasetArtifact.from_dict(r) for r in records)


@dataclass(frozen=True)
class ArtifactVerificationResult:
    artifact_id: str
    found: bool
    fingerprint_unchanged: Optional[bool]
    reconstruction_version_unchanged: Optional[bool]
    schema_compatible: Optional[bool]
    ingestion_runs_still_present: Optional[bool]
    rebuilt_fingerprint: Optional[str]
    reasons: Tuple[str, ...]

    @property
    def is_valid(self) -> bool:
        return self.found and not self.reasons

    def to_dict(self) -> dict:
        return {
            "artifact_id": self.artifact_id, "found": self.found, "is_valid": self.is_valid,
            "fingerprint_unchanged": self.fingerprint_unchanged,
            "reconstruction_version_unchanged": self.reconstruction_version_unchanged,
            "schema_compatible": self.schema_compatible,
            "ingestion_runs_still_present": self.ingestion_runs_still_present,
            "rebuilt_fingerprint": self.rebuilt_fingerprint, "reasons": list(self.reasons),
        }


def verify_artifact(
    artifact_id: str, *, artifact_store: DatasetArtifactStore, historical_store: HistoricalObservationStore,
    now: Optional[datetime.datetime] = None,
) -> ArtifactVerificationResult:
    """Independently REBUILDS the dataset from the artifact's own
    recorded `date_range`/`resolution`/`as_of_time_of_day` and compares
    against what was stored -- never trusts the stored record alone.
    This is the direct reproducibility proof PHASE_18_11 §4 asked for,
    made callable rather than only demonstrable by hand."""
    stored = get_artifact(artifact_id, artifact_store=artifact_store)
    if stored is None:
        return ArtifactVerificationResult(
            artifact_id=artifact_id, found=False, fingerprint_unchanged=None,
            reconstruction_version_unchanged=None, schema_compatible=None,
            ingestion_runs_still_present=None, rebuilt_fingerprint=None, reasons=("artifact_not_found",),
        )

    rebuilt_version = build_dataset_version(
        stored.date_range[0], stored.date_range[1], historical_store=historical_store,
        resolution=stored.resolution, as_of_time_of_day=stored.as_of_time_of_day, now=now,
    )
    rebuilt_identity = derive_dataset_identity(rebuilt_version, schema_version=CURRENT_SCHEMA_VERSION)

    fingerprint_unchanged = rebuilt_identity.fingerprint == stored.fingerprint
    reconstruction_version_unchanged = rebuilt_version.reconstruction_version == stored.reconstruction_version
    schema_compatible = rebuilt_identity.schema_version == stored.schema_version
    # "still exist" = every referenced run id is present in a fresh read today;
    # growth (more runs since) is fine, shrinkage/absence is the real finding.
    ingestion_runs_still_present = set(stored.ingestion_run_references) <= set(rebuilt_version.ingestion_run_references)

    reasons = []
    if not fingerprint_unchanged:
        reasons.append("fingerprint_changed")
    if not reconstruction_version_unchanged:
        reasons.append("reconstruction_version_changed")
    if not schema_compatible:
        reasons.append("schema_version_changed")
    if not ingestion_runs_still_present:
        reasons.append("referenced_ingestion_runs_missing")

    return ArtifactVerificationResult(
        artifact_id=artifact_id, found=True,
        fingerprint_unchanged=fingerprint_unchanged,
        reconstruction_version_unchanged=reconstruction_version_unchanged,
        schema_compatible=schema_compatible, ingestion_runs_still_present=ingestion_runs_still_present,
        rebuilt_fingerprint=rebuilt_identity.fingerprint, reasons=tuple(reasons),
    )


# --- PHASE_18_14: lifecycle transitions ---------------------------------------
def get_lifecycle_state(artifact_id: str, *, lifecycle_store: DatasetLifecycleStore) -> Optional[str]:
    """The CURRENT state is always DERIVED from the latest event --
    never stored as a mutable column anywhere (see
    `dataset_lifecycle_store`'s own module docstring). Returns `None`
    (not `STATE_CREATED`) if no event exists at all -- distinct from a
    real `CREATED` state, since an artifact created without a
    `lifecycle_store` (every pre-18.14 caller) genuinely has no
    recorded lifecycle history to report, and guessing one would be a
    fabrication this project's own discipline forbids."""
    latest = lifecycle_store.latest_event_for(artifact_id)
    return latest["to_state"] if latest is not None else None


def transition_lifecycle_state(
    artifact_id: str, to_state: str, *,
    lifecycle_store: DatasetLifecycleStore, reason: Optional[str] = None,
    now: Optional[datetime.datetime] = None,
) -> dict:
    """The ONLY way a lifecycle state advances. Validates the CURRENT
    state (re-read fresh, never cached) against
    `ALLOWED_LIFECYCLE_TRANSITIONS` and raises
    `InvalidLifecycleTransitionError` for anything not explicitly
    listed -- including transitioning an artifact with no recorded
    history at all (it must first exist via `create_artifact(...,
    lifecycle_store=...)`)."""
    if to_state not in ALL_LIFECYCLE_STATES:
        raise InvalidLifecycleTransitionError(f"unknown target state {to_state!r}")
    current = get_lifecycle_state(artifact_id, lifecycle_store=lifecycle_store)
    if current is None:
        raise InvalidLifecycleTransitionError(
            f"artifact_id={artifact_id!r} has no recorded lifecycle history -- "
            "cannot transition an artifact that was never created with a lifecycle_store."
        )
    allowed = ALLOWED_LIFECYCLE_TRANSITIONS.get(current, ())
    if to_state not in allowed:
        raise InvalidLifecycleTransitionError(
            f"{current} -> {to_state} is not an allowed transition "
            f"(allowed from {current}: {allowed or '(none -- terminal state)'})"
        )
    occurred_at = (now or datetime.datetime.now(IST)).isoformat()
    event = {"from_state": current, "to_state": to_state, "occurred_at": occurred_at, "reason": reason}
    lifecycle_store.append(artifact_id, occurred_at, event)
    return event


# --- PHASE_18_14: certification gate integration ------------------------------
@dataclass(frozen=True)
class CertificationCheckResult:
    passed: bool
    has_required_observations: bool
    required_instruments_present: bool
    resolution_has_data: bool
    all_dates_ready: bool
    certification_references_present: bool
    certification_gate_status: dict  # {component: (status, ref)} -- real CertificationGate.status_for() results.
    reasons: Tuple[str, ...]

    def to_dict(self) -> dict:
        return {
            "passed": self.passed, "has_required_observations": self.has_required_observations,
            "required_instruments_present": self.required_instruments_present,
            "resolution_has_data": self.resolution_has_data, "all_dates_ready": self.all_dates_ready,
            "certification_references_present": self.certification_references_present,
            "certification_gate_status": self.certification_gate_status, "reasons": list(self.reasons),
        }


def run_certification_checks(
    artifact: DatasetArtifact, *, historical_store: HistoricalObservationStore,
    cert_dir: str, required_instruments: Tuple[str, ...] = DEFAULT_REQUIRED_INSTRUMENTS,
) -> CertificationCheckResult:
    """Research-suitability checks -- distinct from `verify_artifact()`'s
    own consistency checks (PHASE_18_13 §1's own named distinction:
    "verify_artifact() checks consistency but not research
    suitability"). Reuses, invents nothing new:
      * `DatasetVersion.ready_dates`/`incomplete_dates` (Phase 18.7) --
        already computed, never previously consulted by artifact
        creation (PHASE_18_13's own finding).
      * `CertificationGate.status_for()` (Phase 17E/17I, unchanged) --
        the SAME certification mechanism every real ingestion script
        already writes to; not a new certification system.
      * `artifact.certification_references` (Phase 18.12) -- reused as
        a presence check."""
    rebuilt = build_dataset_version(
        artifact.date_range[0], artifact.date_range[1], historical_store=historical_store,
        resolution=artifact.resolution, as_of_time_of_day=artifact.as_of_time_of_day,
    )
    has_required_observations = len(rebuilt.fingerprint_lineage) > 0 and any(
        c in rebuilt.included_components for c in required_instruments
    )
    required_instruments_present = set(required_instruments) <= set(rebuilt.included_components)
    resolution_has_data = rebuilt.resolution == artifact.resolution and len(rebuilt.fingerprint_lineage) > 0
    all_dates_ready = len(rebuilt.ready_dates) > 0 and len(rebuilt.incomplete_dates) == 0
    certification_references_present = len(artifact.certification_references) > 0

    gate = CertificationGate(cert_dir)
    gate_status = {}
    for component in required_instruments:
        access_method = _access_method_for(component, artifact.resolution)
        instrument_type = _INSTRUMENT_TYPE_BY_COMPONENT.get(component)
        if access_method is None or instrument_type is None:
            gate_status[component] = (reality_taxonomy.CERTIFICATION_MISSING, None)
            continue
        gate_status[component] = gate.status_for(access_method, instrument_type)
    certification_gate_all_available = all(
        status == reality_taxonomy.CERTIFIED_AVAILABLE for status, _ref in gate_status.values()
    )

    reasons = []
    if not has_required_observations:
        reasons.append("no_required_observations")
    if not required_instruments_present:
        reasons.append("required_instruments_missing")
    if not resolution_has_data:
        reasons.append("resolution_has_no_data")
    if not all_dates_ready:
        reasons.append("not_all_dates_ready")
    if not certification_references_present:
        reasons.append("no_certification_references")
    if not certification_gate_all_available:
        reasons.append("certification_gate_not_all_available")

    passed = not reasons
    return CertificationCheckResult(
        passed=passed, has_required_observations=has_required_observations,
        required_instruments_present=required_instruments_present, resolution_has_data=resolution_has_data,
        all_dates_ready=all_dates_ready, certification_references_present=certification_references_present,
        certification_gate_status={k: list(v) for k, v in gate_status.items()}, reasons=tuple(reasons),
    )


# --- PHASE_18_14: customer-facing dataset manifest ----------------------------
@dataclass(frozen=True)
class DatasetManifest:
    dataset_name: str
    dataset_id: str
    artifact_id: str
    date_range: Tuple[str, str]
    resolution: str
    instruments: Tuple[str, ...]
    reconstruction_version: str
    schema_version: str
    certification_status: str
    code_identity: str
    ingestion_lineage_summary: dict

    def to_dict(self) -> dict:
        return {
            "dataset_name": self.dataset_name, "dataset_id": self.dataset_id,
            "artifact_id": self.artifact_id, "coverage": {"from": self.date_range[0], "to": self.date_range[1]},
            "resolution": self.resolution, "instruments": list(self.instruments),
            "reconstruction_version": self.reconstruction_version, "schema_version": self.schema_version,
            "certification_status": self.certification_status, "code_identity": self.code_identity,
            "ingestion_lineage_summary": self.ingestion_lineage_summary,
        }


def build_manifest(
    artifact: DatasetArtifact, *, dataset_name: str,
    instruments: Tuple[str, ...] = (),
    lifecycle_store: Optional[DatasetLifecycleStore] = None,
) -> DatasetManifest:
    """Generated ENTIRELY from already-real `DatasetArtifact` fields --
    no new storage, per this phase's own instruction. `instruments`
    (PHASE_18_13's own confirmed gap -- never copied onto the artifact
    itself) must be supplied by the caller, typically read straight off
    the `DatasetVersion.included_components` that was used to build the
    artifact; NOT re-derived here by a fresh, costly rebuild, since a
    manifest is a presentation view, not a second reconstruction.
    `certification_status` is the artifact's CURRENT lifecycle state if
    a `lifecycle_store` is supplied (honest: "UNKNOWN" if none is, or
    if no lifecycle history was ever recorded for this artifact --
    never silently defaulted to "CERTIFIED")."""
    certification_status = "UNKNOWN"
    if lifecycle_store is not None:
        state = get_lifecycle_state(artifact.artifact_id, lifecycle_store=lifecycle_store)
        certification_status = state if state is not None else "UNKNOWN"

    return DatasetManifest(
        dataset_name=dataset_name, dataset_id=artifact.dataset_id, artifact_id=artifact.artifact_id,
        date_range=artifact.date_range, resolution=artifact.resolution, instruments=tuple(instruments),
        reconstruction_version=artifact.reconstruction_version, schema_version=artifact.schema_version,
        certification_status=certification_status,
        code_identity=artifact.code_identity.get("code_version") or "unresolved",
        ingestion_lineage_summary={
            "ingestion_run_count": len(artifact.ingestion_run_references),
            "certification_reference_count": len(artifact.certification_references),
        },
    )


# --- PHASE_18_14: backtest eligibility ----------------------------------------
@dataclass(frozen=True)
class BacktestDatasetEligibility:
    artifact_id: str
    eligible: bool
    artifact_exists: bool
    verified: bool
    lifecycle_allows_use: bool
    certification_passed: bool
    completeness_ok: bool
    fingerprint_reproducible: bool
    code_identity_available: bool
    lineage_valid: bool
    reasons: Tuple[str, ...]

    def to_dict(self) -> dict:
        return {
            "artifact_id": self.artifact_id, "eligible": self.eligible,
            "artifact_exists": self.artifact_exists, "verified": self.verified,
            "lifecycle_allows_use": self.lifecycle_allows_use,
            "certification_passed": self.certification_passed, "completeness_ok": self.completeness_ok,
            "fingerprint_reproducible": self.fingerprint_reproducible,
            "code_identity_available": self.code_identity_available, "lineage_valid": self.lineage_valid,
            "reasons": list(self.reasons),
        }


def check_backtest_eligibility(
    artifact_id: str, *,
    artifact_store: DatasetArtifactStore, historical_store: HistoricalObservationStore,
    lifecycle_store: DatasetLifecycleStore, cert_dir: str,
    required_instruments: Tuple[str, ...] = DEFAULT_REQUIRED_INSTRUMENTS,
) -> BacktestDatasetEligibility:
    """The single read-only "is this dataset safe to use" answer a
    future backtester should call BEFORE simulating anything --
    combines checks that already exist (`verify_artifact()`,
    `run_certification_checks()`, lifecycle state) rather than
    inventing a parallel verification mechanism. Never executes a
    strategy, never touches Reality data, never mutates anything."""
    artifact = get_artifact(artifact_id, artifact_store=artifact_store)
    if artifact is None:
        return BacktestDatasetEligibility(
            artifact_id=artifact_id, eligible=False, artifact_exists=False, verified=False,
            lifecycle_allows_use=False, certification_passed=False, completeness_ok=False,
            fingerprint_reproducible=False, code_identity_available=False, lineage_valid=False,
            reasons=("artifact_not_found",),
        )

    verification = verify_artifact(artifact_id, artifact_store=artifact_store, historical_store=historical_store)
    lifecycle_state = get_lifecycle_state(artifact_id, lifecycle_store=lifecycle_store)
    lifecycle_allows_use = lifecycle_state in LIFECYCLE_STATES_ALLOWING_RESEARCH_USE
    cert_result = run_certification_checks(
        artifact, historical_store=historical_store, cert_dir=cert_dir, required_instruments=required_instruments,
    )
    code_identity_available = bool(artifact.code_identity.get("resolved"))
    lineage_valid = bool(verification.ingestion_runs_still_present) and len(artifact.ingestion_run_references) > 0

    reasons = []
    if not verification.is_valid:
        reasons.append("artifact_verification_failed")
    if not lifecycle_allows_use:
        reasons.append(f"lifecycle_state_{lifecycle_state or 'UNKNOWN'}_does_not_allow_research_use")
    if not cert_result.passed:
        reasons.append("certification_checks_failed")
    if not code_identity_available:
        reasons.append("code_identity_unresolved")
    if not lineage_valid:
        reasons.append("lineage_invalid_or_empty")

    eligible = not reasons
    return BacktestDatasetEligibility(
        artifact_id=artifact_id, eligible=eligible, artifact_exists=True,
        verified=verification.is_valid, lifecycle_allows_use=lifecycle_allows_use,
        certification_passed=cert_result.passed, completeness_ok=cert_result.all_dates_ready,
        fingerprint_reproducible=bool(verification.fingerprint_unchanged),
        code_identity_available=code_identity_available, lineage_valid=lineage_valid,
        reasons=tuple(reasons),
    )
