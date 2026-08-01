"""Position Group Journal — BUJJI Options OS v3, Numeric Risk Governor
Gate A.

Authoritative, durable event store for position-group identity and
lifecycle. SQLite, not JSONL — idempotency, sequence allocation, and
state-dependent validation are all performed INSIDE one BEGIN
IMMEDIATE transaction per append, never before it and never via an
application-level check-then-write race.

Order of operations inside `append_event`'s transaction (per the Gate
A contract's final amendment):
  1. check idempotency_key -- if already present, ROLLBACK, return
     None unconditionally. A replay of an already-recorded event must
     succeed even if it would no longer be a legal successor of the
     CURRENT folded state -- it is reporting a fact that already
     happened, not proposing a new transition.
  2. only if absent: read this group's event stream, fold it, validate
     the new event against that exact state, allocate the next
     sequence number, insert, commit.

`append_linked_events` extends this to a cross-group batch: one
BEGIN IMMEDIATE transaction, a batch-wide idempotency pre-check
(all-present -> no-op, some-present -> PartialLinkedBatchIntegrityError,
none-present -> proceed), explicit cross-event linkage validation
(TARGET_GROUP_REDUCTION_APPLIED <-> its matching FILL_OBSERVED), then
per-spec validation against a STAGED combined view that applies each
earlier spec in the batch before validating each later one.

Process-level single-writer safety: `process_lock`, when supplied,
must already be held (`process_lock.held is True`) or construction
raises immediately. This journal never calls `acquire()`/`release()`
itself -- process lifecycle belongs to the caller. `process_lock=None`
is accepted only for read-only/unit-test construction; a caller
building a dispatch- or recovery-capable journal for the real runtime
must pass an already-acquired lock (enforced by the production
composition boundary that constructs this journal for real use, not
by this class refusing to be instantiated at all).
"""
from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union

from bujji.trading_brain.risk_governor.position_group_fold import (
    PositionGroupState,
    apply_event_to_state,
    fold,
)
from bujji.trading_brain.risk_governor.position_group_validation import (
    IllegalEventError,
    validate_event,
)

SCHEMA_VERSION = "1.0.0"

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS position_group_events (
    event_id             TEXT PRIMARY KEY,
    position_group_id    TEXT NOT NULL,
    sequence_no           INTEGER NOT NULL,
    event_type            TEXT NOT NULL,
    idempotency_key         TEXT NOT NULL UNIQUE,
    recorded_at             TEXT NOT NULL,
    payload_json             TEXT NOT NULL,
    schema_version            TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_group_sequence
    ON position_group_events(position_group_id, sequence_no);
"""

_MINT_UNIQUENESS_SQL = """
CREATE UNIQUE INDEX IF NOT EXISTS ux_mint_per_plan
    ON position_group_events(json_extract(payload_json, '$.plan_id'))
    WHERE event_type = 'MINTED';
"""


class SequenceAllocationInvariantViolation(RuntimeError):
    """The (position_group_id, sequence_no) unique index rejected an
    insert already serialized through BEGIN IMMEDIATE-guarded
    allocation -- the serialization guarantee itself was violated.
    Never retried automatically."""


class MintUniquenessViolation(RuntimeError):
    """A DIFFERENT, correctly-sequenced position_group_id lost a
    legitimate race against a concurrent mint for the same plan_id.
    An expected business-rule collision, not an internal invariant
    violation -- callers must re-query find_mint_by_plan_id()."""


class JournalCorruptError(RuntimeError):
    """PRAGMA integrity_check failed at open(). The journal refuses to
    serve any read or write until an operator resolves this."""


class ProcessLockNotHeldError(RuntimeError):
    """A process_lock was supplied but is not currently held -- this
    journal refuses to open for writing without proof of exclusive
    process ownership."""


class PartialLinkedBatchIntegrityError(RuntimeError):
    """A linked batch was found with SOME but not ALL of its
    idempotency keys already recorded. Impossible under this
    transactional design (one BEGIN IMMEDIATE covers the whole batch)
    unless an earlier attempt used a different/broken write path, or
    genuine corruption occurred. Never silently completed from
    assumption -- requires operator/recovery handling."""

    def __init__(self, recorded_keys: List[str], missing_keys: List[str]) -> None:
        self.recorded_keys = recorded_keys
        self.missing_keys = missing_keys
        super().__init__(
            f"partial linked batch: {len(recorded_keys)} recorded, {len(missing_keys)} missing "
            f"-- {missing_keys}"
        )


class IdempotencyKeyCollisionError(RuntimeError):
    """The idempotency_key already exists but for a DIFFERENT
    (position_group_id, event_type, canonical payload) -- a genuine key
    collision, not a replay. Never silently treated as a no-op; the
    caller has a real bug (key reuse) or a real anomaly to investigate."""

    def __init__(self, idempotency_key: str, existing, proposed) -> None:
        self.idempotency_key = idempotency_key
        self.existing = existing
        self.proposed = proposed
        super().__init__(
            f"idempotency_key {idempotency_key!r} collision: existing={existing!r}, proposed={proposed!r}"
        )


class LinkageValidationError(RuntimeError):
    """A TARGET_GROUP_REDUCTION_APPLIED event in a linked batch could
    not be matched, or disagreed, with its claimed source FILL_OBSERVED
    event, or its quantity exceeds what the source fill or target
    group's current net quantity can support. The entire batch is
    rejected -- nothing is inserted."""


@dataclass(frozen=True)
class PositionGroupEvent:
    event_id: str
    position_group_id: str
    sequence_no: int
    event_type: str
    idempotency_key: str
    recorded_at: datetime
    payload: Dict[str, Any] = field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION


@dataclass(frozen=True)
class EventSpec:
    position_group_id: str
    event_type: str
    idempotency_key: str
    payload: Dict[str, Any]


class ProcessLockLike:
    """Structural type this module depends on -- matches
    bujji.core.process_lock.ProcessLock's public surface (`held`
    property) without importing it, so this module has no hard
    dependency on that package."""

    held: bool


class PositionGroupJournal:
    def __init__(
        self,
        path: Union[str, Path],
        process_lock: Optional[ProcessLockLike] = None,
    ) -> None:
        import threading

        if process_lock is not None and not process_lock.held:
            raise ProcessLockNotHeldError(
                "process_lock was supplied but is not currently held -- acquire() it "
                "before constructing a dispatch- or recovery-capable journal"
            )
        self._process_lock = process_lock
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            str(self._path), isolation_level=None, timeout=5.0, check_same_thread=False
        )
        self._write_lock = threading.Lock()
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=FULL")
        self._conn.executescript(_SCHEMA_SQL)
        self._conn.execute(_MINT_UNIQUENESS_SQL)
        self._check_integrity()

    def _check_integrity(self) -> None:
        row = self._conn.execute("PRAGMA integrity_check").fetchone()
        if row is None or row[0] != "ok":
            raise JournalCorruptError(f"PRAGMA integrity_check reported: {row}")

    def close(self) -> None:
        self._conn.close()

    def _run_transaction(self, txn_fn: Callable[[], Any], attempts: int = 5, base_delay_s: float = 0.01) -> Any:
        """Explicit bounded retry loop around a plain callable -- NOT a
        multi-yield context manager. A generator-based contextmanager
        can yield exactly once per `with` block; retrying by yielding
        again from inside an `except` clause is invalid and raises
        from contextlib itself the first time it's actually exercised
        under real contention. This replaces that earlier, untested-
        under-real-SQLITE_BUSY design."""
        last_exc: Optional[Exception] = None
        for attempt in range(attempts):
            try:
                return txn_fn()
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower() and "busy" not in str(exc).lower():
                    raise
                last_exc = exc
                time.sleep(base_delay_s * (2 ** attempt))
        raise last_exc  # type: ignore[misc]

    # ------------------------------------------------------------------ #
    # Single-event append
    # ------------------------------------------------------------------ #
    def append_event(
        self,
        position_group_id: str,
        event_type: str,
        idempotency_key: str,
        payload: Dict[str, Any],
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> Optional[PositionGroupEvent]:
        recorded_at = clock()

        def _txn() -> Optional[PositionGroupEvent]:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                existing = self._conn.execute(
                    "SELECT position_group_id, event_type, payload_json "
                    "FROM position_group_events WHERE idempotency_key = ?",
                    (idempotency_key,),
                ).fetchone()
                if existing is not None:
                    proposed_canonical = (position_group_id, event_type, json.dumps(payload, sort_keys=True))
                    if tuple(existing) == proposed_canonical:
                        self._conn.execute("ROLLBACK")
                        return None
                    self._conn.execute("ROLLBACK")
                    raise IdempotencyKeyCollisionError(idempotency_key, existing, proposed_canonical)

                current_state = self._fold_no_lock(position_group_id)
                try:
                    validate_event(event_type, payload, recorded_at, current_state)
                except IllegalEventError:
                    self._conn.execute("ROLLBACK")
                    raise

                next_sequence_no = self._next_sequence_no_no_lock(position_group_id)
                event_id = f"{position_group_id}:{next_sequence_no}"
                try:
                    self._insert_no_lock(
                        event_id, position_group_id, next_sequence_no, event_type,
                        idempotency_key, recorded_at, payload,
                    )
                except sqlite3.IntegrityError as exc:
                    self._conn.execute("ROLLBACK")
                    if event_type == "MINTED" and "ux_mint_per_plan" in str(exc):
                        raise MintUniquenessViolation(
                            f"a concurrent mint for the same plan_id already exists; "
                            f"candidate {position_group_id} lost the race: {exc}"
                        ) from exc
                    raise SequenceAllocationInvariantViolation(
                        f"unique-index rejection after serialized allocation for "
                        f"{position_group_id}#{next_sequence_no}: {exc}"
                    ) from exc

                self._conn.execute("COMMIT")
                return PositionGroupEvent(
                    event_id=event_id, position_group_id=position_group_id,
                    sequence_no=next_sequence_no, event_type=event_type,
                    idempotency_key=idempotency_key, recorded_at=recorded_at, payload=payload,
                )
            except Exception:
                try:
                    self._conn.execute("ROLLBACK")
                except sqlite3.OperationalError:
                    pass
                raise

        with self._write_lock:
            return self._run_transaction(_txn)

    # ------------------------------------------------------------------ #
    # Linked, cross-group batch append
    # ------------------------------------------------------------------ #
    def append_linked_events(
        self,
        specs: List[EventSpec],
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> List[Optional[PositionGroupEvent]]:
        recorded_at = clock()

        def _txn() -> List[Optional[PositionGroupEvent]]:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                present = []
                for spec in specs:
                    row = self._conn.execute(
                        "SELECT position_group_id, event_type, payload_json "
                        "FROM position_group_events WHERE idempotency_key = ?",
                        (spec.idempotency_key,),
                    ).fetchone()
                    if row is None:
                        continue
                    proposed_canonical = (
                        spec.position_group_id, spec.event_type, json.dumps(spec.payload, sort_keys=True)
                    )
                    if tuple(row) != proposed_canonical:
                        self._conn.execute("ROLLBACK")
                        raise IdempotencyKeyCollisionError(spec.idempotency_key, row, proposed_canonical)
                    present.append(spec)

                if len(present) == len(specs):
                    self._conn.execute("ROLLBACK")
                    return [None] * len(specs)
                if 0 < len(present) < len(specs):
                    self._conn.execute("ROLLBACK")
                    raise PartialLinkedBatchIntegrityError(
                        recorded_keys=[s.idempotency_key for s in present],
                        missing_keys=[s.idempotency_key for s in specs if s not in present],
                    )

                staged_states: Dict[str, Optional[PositionGroupState]] = {}

                def _get_staged(gid: str) -> Optional[PositionGroupState]:
                    if gid not in staged_states:
                        staged_states[gid] = self._fold_no_lock(gid)
                    return staged_states[gid]

                try:
                    self._validate_linkage(specs, _get_staged)
                except LinkageValidationError:
                    self._conn.execute("ROLLBACK")
                    raise

                results: List[PositionGroupEvent] = []
                for spec in specs:
                    state = _get_staged(spec.position_group_id)
                    try:
                        validate_event(spec.event_type, spec.payload, recorded_at, state)
                    except IllegalEventError:
                        self._conn.execute("ROLLBACK")
                        raise
                    staged_states[spec.position_group_id] = apply_event_to_state(
                        state, spec.event_type, spec.payload, spec.position_group_id
                    )

                    next_sequence_no = self._next_sequence_no_no_lock(spec.position_group_id)
                    event_id = f"{spec.position_group_id}:{next_sequence_no}"
                    try:
                        self._insert_no_lock(
                            event_id, spec.position_group_id, next_sequence_no, spec.event_type,
                            spec.idempotency_key, recorded_at, spec.payload,
                        )
                    except sqlite3.IntegrityError as exc:
                        self._conn.execute("ROLLBACK")
                        raise SequenceAllocationInvariantViolation(
                            f"unique-index rejection during linked batch for "
                            f"{spec.position_group_id}#{next_sequence_no}: {exc}"
                        ) from exc
                    results.append(PositionGroupEvent(
                        event_id=event_id, position_group_id=spec.position_group_id,
                        sequence_no=next_sequence_no, event_type=spec.event_type,
                        idempotency_key=spec.idempotency_key, recorded_at=recorded_at,
                        payload=spec.payload,
                    ))

                self._conn.execute("COMMIT")
                return results
            except Exception:
                try:
                    self._conn.execute("ROLLBACK")
                except sqlite3.OperationalError:
                    pass
                raise

        with self._write_lock:
            return self._run_transaction(_txn)

    @staticmethod
    def _validate_linkage(specs: List[EventSpec], get_staged) -> None:
        fill_by_coid = {
            s.payload["client_order_id"]: s for s in specs if s.event_type == "FILL_OBSERVED"
        }
        for spec in specs:
            if spec.event_type != "TARGET_GROUP_REDUCTION_APPLIED":
                continue
            p = spec.payload
            source_fill = fill_by_coid.get(p.get("source_client_order_id"))
            if source_fill is None:
                raise LinkageValidationError(
                    "TARGET_GROUP_REDUCTION_APPLIED has no matching FILL_OBSERVED in this batch"
                )
            if source_fill.position_group_id != p.get("source_position_group_id"):
                raise LinkageValidationError(
                    "source_position_group_id disagrees with the matching FILL_OBSERVED's own group"
                )
            source_state = get_staged(source_fill.position_group_id)
            source_leg = source_state.legs.get(p["source_client_order_id"]) if source_state else None
            if source_leg is None:
                raise LinkageValidationError("source leg not found in source group's state")
            if (source_leg.target_position_group_id != spec.position_group_id
                    or source_leg.target_contract_id != p.get("target_contract_id")):
                raise LinkageValidationError(
                    "target group/contract mapping disagrees with CONSTRUCTED-time attribution"
                )
            delta = p.get("reduced_quantity_delta")
            if delta is None or delta <= 0:
                raise LinkageValidationError("reduction quantity must be positive")
            if delta > source_fill.payload.get("delta_quantity", 0):
                raise LinkageValidationError("reduction exceeds the source fill's own delta_quantity")
            target_state = get_staged(spec.position_group_id)
            if target_state is None:
                raise LinkageValidationError("target group has no prior state to reduce")
            target_leg = None
            for leg in target_state.legs.values():
                if leg.contract_id == p.get("target_contract_id"):
                    target_leg = leg
                    break
            if target_leg is None:
                raise LinkageValidationError("target_contract_id not found in target group")
            from bujji.trading_brain.risk_governor.position_group_fold import net_quantity
            if delta > net_quantity(target_leg):
                raise LinkageValidationError("reduction exceeds the target group's current net quantity")

    # ------------------------------------------------------------------ #
    # Internal helpers -- MUST be called only while holding _write_lock
    # and inside an open transaction; no locking of their own.
    # ------------------------------------------------------------------ #
    def _fold_no_lock(self, position_group_id: str) -> Optional[PositionGroupState]:
        events = self._read_events_no_lock(position_group_id)
        return fold(events) if events else None

    def _read_events_no_lock(self, position_group_id: str) -> List[PositionGroupEvent]:
        rows = self._conn.execute(
            "SELECT event_id, position_group_id, sequence_no, event_type, "
            "idempotency_key, recorded_at, payload_json, schema_version "
            "FROM position_group_events WHERE position_group_id = ? ORDER BY sequence_no ASC",
            (position_group_id,),
        ).fetchall()
        return [self._row_to_event(r) for r in rows]

    def _next_sequence_no_no_lock(self, position_group_id: str) -> int:
        row = self._conn.execute(
            "SELECT COALESCE(MAX(sequence_no), -1) FROM position_group_events WHERE position_group_id = ?",
            (position_group_id,),
        ).fetchone()
        return row[0] + 1

    def _insert_no_lock(self, event_id, position_group_id, sequence_no, event_type,
                         idempotency_key, recorded_at, payload) -> None:
        self._conn.execute(
            "INSERT INTO position_group_events "
            "(event_id, position_group_id, sequence_no, event_type, "
            " idempotency_key, recorded_at, payload_json, schema_version) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                event_id, position_group_id, sequence_no, event_type, idempotency_key,
                recorded_at.isoformat(), json.dumps(payload, sort_keys=True), SCHEMA_VERSION,
            ),
        )

    # ------------------------------------------------------------------ #
    # Public read API (locked, no open transaction assumed)
    # ------------------------------------------------------------------ #
    def read_events(self, position_group_id: str) -> List[PositionGroupEvent]:
        with self._write_lock:
            return self._read_events_no_lock(position_group_id)

    def read_all_group_ids(self) -> List[str]:
        with self._write_lock:
            rows = self._conn.execute(
                "SELECT DISTINCT position_group_id FROM position_group_events ORDER BY position_group_id"
            ).fetchall()
        return [r[0] for r in rows]

    def find_mint_by_plan_id(self, plan_id: str) -> Optional[PositionGroupEvent]:
        with self._write_lock:
            row = self._conn.execute(
                "SELECT event_id, position_group_id, sequence_no, event_type, "
                "idempotency_key, recorded_at, payload_json, schema_version "
                "FROM position_group_events "
                "WHERE event_type = 'MINTED' AND json_extract(payload_json, '$.plan_id') = ?",
                (plan_id,),
            ).fetchone()
        return self._row_to_event(row) if row is not None else None

    @staticmethod
    def _row_to_event(row) -> PositionGroupEvent:
        (event_id, position_group_id, sequence_no, event_type,
         idempotency_key, recorded_at, payload_json, schema_version) = row
        return PositionGroupEvent(
            event_id=event_id, position_group_id=position_group_id, sequence_no=sequence_no,
            event_type=event_type, idempotency_key=idempotency_key,
            recorded_at=datetime.fromisoformat(recorded_at),
            payload=json.loads(payload_json), schema_version=schema_version,
        )
