"""Observation Memory Recovery -- Phase 15D.

Forensic finding (real-data validated against the full 174-cycle
SHADOW-OBSERVATORY-2026-08-06 session, 0 mismatches): ObservationMemory
needs NO new persistence format at all. `market_snapshots.jsonl` is
already persisted every cycle by ShadowSessionRunner, already contains
every MarketSnapshot in order, and replaying those snapshots through a
FRESH `MarketStateBuilder.process()` -- the exact same pure function
the live recorder already calls every cycle -- deterministically
reproduces PSI/MSSI/episodes/event_history byte-for-byte. This is
event-derived reconstruction in its purest form: the "event log" is
simply the market data itself, and the "replay function" is the
production pipeline itself, unmodified.

Torn/malformed/schema-mismatched/duplicate-or-out-of-order lines are
diagnosed and skipped (never fabricated, never silently reordered),
mirroring the diagnostics pattern `state_persistence.store.EventStore`
already established -- reused here as the established pattern to
follow (per Phase 15D's explicit "prefer existing infrastructure"
instruction), not duplicated as a competing mechanism, since
`market_snapshots.jsonl` is written by plain JSONL append (not
EventStore) and has no event_id, only a real, always-increasing
per-cycle `timestamp`.

Session isolation is structural, not field-based: unlike
state_persistence's shared EventStore files, `market_snapshots.jsonl`
already lives at one path per session directory
(`shadow_sessions/<session_id>/market_snapshots.jsonl`) -- a session
cannot see another session's snapshots because it never reads another
session's file.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Iterator, Optional, Tuple

from bujji.market_perception.models import (
    SNAPSHOT_VERSION, FutureSnapshot, MarketSnapshot, OptionChainConfig,
    OptionChainSnapshot, OptionLeg, SpotSnapshot, VixSnapshot,
)
from bujji.market_state_builder.market_state import MarketStateBuilder, ObservationMemory
from bujji.state_persistence.models import (
    RECOVERY_COMPLETE, RECOVERY_FAILED, RECOVERY_PARTIAL, RecoveryReport,
)

ISSUE_MALFORMED = "malformed"
ISSUE_SCHEMA_MISMATCH = "schema_mismatch"
ISSUE_DUPLICATE_OR_OUT_OF_ORDER = "duplicate_or_out_of_order"


def market_snapshot_from_dict(d: dict) -> MarketSnapshot:
    """Inverse of `dataclasses.asdict(MarketSnapshot)` -- reconstructs
    the real, immutable dataclass this project already persists via
    `_snapshot_to_dict` in shadow_session_runner.py. Raises
    KeyError/TypeError/ValueError on a genuinely malformed/incompatible
    record -- the caller (`read_market_snapshots_with_diagnostics`)
    catches this and reports it, never guesses a substitute value."""
    spot = SpotSnapshot(**d["spot"]) if d.get("spot") else None
    vix = VixSnapshot(**d["vix"]) if d.get("vix") else None
    futures = FutureSnapshot(**d["futures"]) if d.get("futures") else None
    chain = None
    if d.get("option_chain"):
        c = d["option_chain"]
        config = OptionChainConfig(**c["config"])
        legs = tuple(OptionLeg(**leg) for leg in c["legs"])
        chain = OptionChainSnapshot(
            underlying=c["underlying"], expiry=c["expiry"], atm_strike=c["atm_strike"],
            config=config, legs=legs,
        )
    return MarketSnapshot(
        snapshot_version=d["snapshot_version"], timestamp=d["timestamp"], source=d["source"],
        latency_ms=d["latency_ms"], health_status=d["health_status"],
        missing_fields=tuple(d.get("missing_fields", ())),
        spot=spot, vix=vix, futures=futures, option_chain=chain,
    )


def read_market_snapshots_with_diagnostics(
    path: str,
) -> Iterator[Tuple[Optional[MarketSnapshot], Optional[str]]]:
    """Yields (snapshot, None) for each real, valid, in-order line, or
    (None, issue) for a torn/malformed/schema-mismatched/duplicate-or-
    out-of-order one -- never raises, mirrors
    `EventStore.read_events_with_diagnostics`'s contract. Order is
    NEVER altered: a stale/duplicate timestamp is skipped and reported,
    not silently reordered ahead of where it appeared."""
    if not path or not os.path.exists(path):
        return
    last_timestamp: Optional[str] = None
    with open(path) as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                yield None, ISSUE_MALFORMED
                continue
            try:
                snapshot = market_snapshot_from_dict(d)
            except (KeyError, TypeError, ValueError):
                yield None, ISSUE_SCHEMA_MISMATCH
                continue
            if snapshot.snapshot_version != SNAPSHOT_VERSION:
                yield None, ISSUE_SCHEMA_MISMATCH
                continue
            if last_timestamp is not None and snapshot.timestamp <= last_timestamp:
                yield None, ISSUE_DUPLICATE_OR_OUT_OF_ORDER
                continue
            last_timestamp = snapshot.timestamp
            yield snapshot, None


@dataclass(frozen=True)
class ReplayOutcome:
    """Everything both callers need from one accumulation pass.

    `last_assessment` exists because the stability gate reads the FINAL
    MarketStateAssessment, while recovery only wants the memory -- without
    it the gate had to run its own duplicate loop, which is exactly what
    this function was extracted to prevent.
    """
    memory: object
    replayed: int
    errors: tuple
    last_assessment: object
    last_timestamp: Optional[str]


def replay_snapshots(snapshots, builder: Optional[MarketStateBuilder] = None) -> "ReplayOutcome":
    """Run snapshots through ONE builder, in order. Returns a ReplayOutcome.

    THE SINGLE ACCUMULATION LOOP. Price structure is cross-cycle state:
    `MarketStateBuilder` holds its own ObservationMemory and only emits a
    PRICE_CHANGED when a spot value differs from the PREVIOUS one it saw.
    A fresh builder per snapshot therefore accumulates nothing, which is
    why every caller must share one instance -- and why this loop exists
    once rather than at each call site.

    `hydrate_observation_memory` below delegates here after its file-level
    filtering, so the file-backed and in-memory paths cannot drift apart.

    KNOWN THIRD COPY, NOT CONSOLIDATED HERE: `bujji/replay_engine/engine.py`
    (around lines 123-146) runs its own `MarketStateBuilder()` + process
    loop with its own diagnostics counters. Folding it in touches the
    replay engine's public reporting contract, so it is recorded rather
    than silently left unmentioned.

    A snapshot that raises is recorded and skipped -- one corrupt snapshot
    must not discard the evidence already accumulated before it.
    """
    builder = builder if builder is not None else MarketStateBuilder()
    replayed = 0
    errors = []
    last_assessment = None
    last_timestamp = None
    for snapshot in snapshots:
        try:
            last_assessment = builder.process(snapshot)
            replayed += 1
            last_timestamp = snapshot.timestamp
        except Exception as exc:  # noqa: BLE001 -- degrade, never crash the caller.
            errors.append(f"{type(exc).__name__}: {exc}")
    return ReplayOutcome(builder.memory, replayed, tuple(errors), last_assessment, last_timestamp)


def hydrate_observation_memory(market_snapshot_path: Optional[str]) -> Tuple[ObservationMemory, RecoveryReport]:
    """Replays every real, valid, in-order MarketSnapshot from
    `market_snapshot_path` through a FRESH MarketStateBuilder --
    the exact same `.process()` the live recorder already calls each
    cycle -- and returns the resulting ObservationMemory plus an
    explicit RecoveryReport. Never reads live market data; a missing
    file is a real, honest RECOVERY_COMPLETE (nothing to recover), not
    an error."""
    builder = MarketStateBuilder()
    total_lines = 0
    malformed = 0
    schema_mismatch = 0
    duplicate_or_out_of_order = 0
    replayed = 0
    errors = []
    last_ts: Optional[str] = None

    # File-level triage stays here (it is what makes this the FILE entry
    # point); the accumulation itself is delegated, so this path and the
    # in-memory one cannot drift apart.
    accepted = []
    for snapshot, issue in read_market_snapshots_with_diagnostics(market_snapshot_path):
        total_lines += 1
        if issue == ISSUE_MALFORMED:
            malformed += 1
            continue
        if issue == ISSUE_SCHEMA_MISMATCH:
            schema_mismatch += 1
            continue
        if issue == ISSUE_DUPLICATE_OR_OUT_OF_ORDER:
            duplicate_or_out_of_order += 1
            continue
        accepted.append(snapshot)

    outcome = replay_snapshots(accepted, builder)
    replayed = outcome.replayed
    errors = list(outcome.errors)
    last_ts = outcome.last_timestamp

    had_issues = bool(errors or malformed or schema_mismatch or duplicate_or_out_of_order)
    if total_lines == 0:
        status = RECOVERY_COMPLETE
    elif errors and replayed == 0:
        status = RECOVERY_FAILED
    elif had_issues and replayed == 0:
        status = RECOVERY_FAILED
    elif had_issues:
        status = RECOVERY_PARTIAL
    else:
        status = RECOVERY_COMPLETE

    report = RecoveryReport(
        status=status, events_discovered=total_lines, events_replayed=replayed,
        events_skipped_duplicate=duplicate_or_out_of_order, events_skipped_malformed=malformed,
        events_skipped_schema_mismatch=schema_mismatch, last_recovered_cycle_id=last_ts,
        errors=tuple(errors),
        unresolved_notes=() if not had_issues else (
            f"{malformed} malformed, {schema_mismatch} schema-mismatched, "
            f"{duplicate_or_out_of_order} duplicate/out-of-order line(s) skipped",
        ),
    )
    return builder.memory, report


def observation_memory_fingerprint(memory: ObservationMemory) -> str:
    """Deterministic state fingerprint -- same ObservationMemory content
    always produces the same hash, regardless of tuple-vs-list identity
    or object identity. Used to prove state equivalence (e.g. an
    uninterrupted run vs. a killed-and-restarted-then-hydrated run)
    without a human diffing two large nested dataclasses by hand."""
    import dataclasses
    import hashlib

    payload = json.dumps(dataclasses.asdict(memory), sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()
