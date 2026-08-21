"""Observation validator — Phase 17E. Pure functions, zero IO.

The gate every observation passes before it is permitted into Layer 0.
Certification status is PASSED IN rather than looked up here, so this
module stays deterministic and unit-testable without touching the
filesystem -- the store resolves certification and hands it over.

Five checks, per Phase 17E scope item 3:
  1. symbol identity
  2. timestamp ordering
  3. schema validity
  4. certification status
  5. duplicate detection   (delegated to the store, which alone knows
                            what it already holds -- see check_duplicate)

Every check runs unconditionally -- never short-circuiting on the first
failure -- so `reasons` is always the COMPLETE set of what is wrong with
a candidate, not merely the first thing noticed. This mirrors
`market_observation.engine.validate_observation()`'s established rule.

No wall-clock is read anywhere in this module. The comparison "now" is
always injected by the caller; replay-safety depends on it.
"""
from __future__ import annotations

import datetime
from typing import Any, List, Mapping, Optional

from . import taxonomy
from .models import RawObservation, ValidationOutcome


def _is_well_formed_timestamp(value: Any) -> bool:
    """ISO-8601, parseable. Deliberately the same rule
    `market_observation.engine._is_well_formed_timestamp` applies,
    rather than a second, subtly-different parser."""
    if not value or not isinstance(value, str):
        return False
    try:
        datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return False
    return True


def _parse(value: str) -> Optional[datetime.datetime]:
    try:
        return datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _comparable(a: datetime.datetime, b: datetime.datetime) -> bool:
    """Two datetimes can only be ordered if both are naive or both are
    aware. Mixing them raises in Python; rather than crash on a
    tz-inconsistent candidate we decline to compare and let the
    well-formedness checks carry the signal."""
    return (a.tzinfo is None) == (b.tzinfo is None)


def check_symbol_identity(raw: RawObservation) -> List[str]:
    """Instrument present, kind and instrument_type in their closed sets,
    and every identity field that instrument type requires actually
    present. A future without an expiry, or an option without a
    strike/right, is not an identifiable contract and cannot be stored as
    though it were."""
    reasons: List[str] = []

    if raw.kind not in taxonomy.ALL_OBSERVATION_KINDS:
        reasons.append(taxonomy.REJECT_UNKNOWN_KIND)

    if raw.instrument_type not in taxonomy.ALL_INSTRUMENT_TYPES:
        reasons.append(taxonomy.REJECT_UNKNOWN_INSTRUMENT_TYPE)

    if not raw.instrument:
        reasons.append(taxonomy.REJECT_MISSING_INSTRUMENT)

    required = taxonomy.REQUIRED_IDENTITY_FIELDS.get(raw.instrument_type, ())
    for field_name in required:
        value = raw.identity_fields.get(field_name)
        if value is None or (isinstance(value, str) and not value.strip()):
            reasons.append(f"{taxonomy.REJECT_MISSING_IDENTITY_FIELD}:{field_name}")

    if raw.instrument_type == taxonomy.INSTRUMENT_OPTION:
        option_type = raw.identity_fields.get("option_type")
        if option_type is not None and option_type not in taxonomy.ALL_OPTION_TYPES:
            reasons.append(taxonomy.REJECT_UNKNOWN_OPTION_TYPE)

    return reasons


def check_timestamps(raw: RawObservation, now: str) -> List[str]:
    """Capture timestamp must exist and be well-formed. Event timestamp
    may legitimately be absent (a bare LTP poll publishes none) but must
    be well-formed if present. An event may not post-date its own
    capture, and a capture may not be in the future relative to the
    injected `now`."""
    reasons: List[str] = []
    lineage = raw.lineage

    if not lineage.capture_timestamp:
        reasons.append(taxonomy.REJECT_MISSING_CAPTURE_TIMESTAMP)
        return reasons

    if not _is_well_formed_timestamp(lineage.capture_timestamp):
        reasons.append(taxonomy.REJECT_MALFORMED_CAPTURE_TIMESTAMP)

    if lineage.event_timestamp is not None:
        if not _is_well_formed_timestamp(lineage.event_timestamp):
            reasons.append(taxonomy.REJECT_MALFORMED_EVENT_TIMESTAMP)

    captured = _parse(lineage.capture_timestamp)
    evented = _parse(lineage.event_timestamp) if lineage.event_timestamp else None
    now_dt = _parse(now)

    if captured is not None and evented is not None and _comparable(captured, evented):
        if evented > captured:
            reasons.append(taxonomy.REJECT_EVENT_AFTER_CAPTURE)

    if captured is not None and now_dt is not None and _comparable(captured, now_dt):
        if captured > now_dt:
            reasons.append(taxonomy.REJECT_CAPTURE_IN_FUTURE)

    return reasons


def check_schema(raw: RawObservation) -> List[str]:
    """Recognized schema version, required payload keys present, and NO
    forbidden derived field anywhere in the payload.

    Present-but-zero is explicitly valid: a genuinely zero bid/ask on an
    illiquid contract is a true market fact (verified live 2026-08-12 on
    a far-OTM NIFTY option) and is stored as one. Only ABSENCE is a
    failure -- absent and zero are never conflated.
    """
    reasons: List[str] = []

    if raw.lineage.schema_version not in taxonomy.RECOGNIZED_LAYER0_SCHEMA_VERSIONS:
        reasons.append(taxonomy.REJECT_UNRECOGNIZED_SCHEMA_VERSION)

    payload = raw.payload
    if payload is None:
        reasons.append(taxonomy.REJECT_EMPTY_PAYLOAD)
        return reasons

    if isinstance(payload, Mapping):
        required = taxonomy.REQUIRED_PAYLOAD_FIELDS.get(raw.kind, ())
        for key in required:
            if key not in payload:
                reasons.append(f"{taxonomy.REJECT_MISSING_REQUIRED_FIELD}:{key}")

        for key in payload:
            if str(key).lower() in taxonomy.FORBIDDEN_PAYLOAD_FIELDS:
                reasons.append(f"{taxonomy.REJECT_FORBIDDEN_DERIVED_FIELD}:{key}")
    elif taxonomy.REQUIRED_PAYLOAD_FIELDS.get(raw.kind, ()):
        # A kind declaring required keys must carry a mapping payload.
        for key in taxonomy.REQUIRED_PAYLOAD_FIELDS[raw.kind]:
            reasons.append(f"{taxonomy.REJECT_MISSING_REQUIRED_FIELD}:{key}")

    return reasons


def check_certification(certification_status: str) -> List[str]:
    """Only CERTIFIED_AVAILABLE permits a Layer 0 write. Every other
    state -- including CERTIFICATION_MISSING -- denies it. Fail closed."""
    if certification_status not in taxonomy.WRITE_PERMITTED_CERTIFICATION_STATES:
        return [f"{taxonomy.REJECT_NOT_CERTIFIED}:{certification_status}"]
    return []


def check_lineage_completeness(raw: RawObservation) -> List[str]:
    """Source and access method must be present -- an observation that
    cannot say where it came from is not auditable, and an unauditable
    observation is not Layer 0 material."""
    reasons: List[str] = []
    if not raw.lineage.source:
        reasons.append(taxonomy.REJECT_MISSING_SOURCE)
    if not raw.lineage.access_method:
        reasons.append(taxonomy.REJECT_MISSING_ACCESS_METHOD)
    return reasons


def validate(
    raw: RawObservation, certification_status: str, now: str
) -> ValidationOutcome:
    """Run every pre-persistence check. `reasons` is always complete.

    Duplicate detection is NOT run here: only the store knows what it
    already holds, and a duplicate is not a validity failure (see
    `store.RawObservationStore.append`). Keeping it out of this function
    is what lets `validate()` stay pure and IO-free.
    """
    reasons: List[str] = []
    reasons.extend(check_symbol_identity(raw))
    reasons.extend(check_timestamps(raw, now))
    reasons.extend(check_schema(raw))
    reasons.extend(check_certification(certification_status))
    reasons.extend(check_lineage_completeness(raw))

    return ValidationOutcome(
        is_valid=not reasons,
        reasons=tuple(reasons),
        validator_version=taxonomy.VALIDATOR_VERSION,
    )
