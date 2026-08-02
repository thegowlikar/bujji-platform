"""Shadow Observatory Recorder -- BUJJI Options OS v3, Gate V.0.

PURPOSE: a passive black-box recorder. Subscribes to every existing
EventType on the EventBus (same `attach()` pattern already established
by ShadowTradeTimeline in F.1) and appends a classified artifact for
each event it receives. It never publishes anything itself, never
calls back into the runtime, and never influences a decision -- pure
observation, per this gate's own explicit "No Intelligence" rule.

FAILURE ISOLATION (Rule 3): `_on_event()` wraps EVERYTHING in a single
try/except. Any exception -- a malformed payload, a disk failure that
`SessionStore` itself didn't already catch, a serialization error --
is caught here, appended to `self.internal_errors` (never raised, and
NEVER logged into the SAME `errors.jsonl` that a caller might filter
trading-relevant errors from, to keep "the recorder broke" and "the
runtime reported a fault" visibly distinct). Runtime execution
continues unaffected either way -- this is enforced structurally
(this method is the ONLY place any exception from this module could
possibly propagate from, and it always suppresses it).
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional, Tuple

from bujji.core.event_bus import Event, EventBus, EventType

from .models import HeartbeatArtifact, SessionManifest, SessionMetadata
from .report_builder import build_session_summary
from .serializers import build_artifact, classify_destinations
from .session_store import SessionStore


class ShadowObservatoryRecorder:
    def __init__(self, store: SessionStore) -> None:
        self._store = store
        self.internal_errors: List[str] = []

    def attach(self, event_bus: EventBus) -> None:
        for event_type in EventType:
            event_bus.subscribe(event_type, self._on_event)

    def _on_event(self, event: Event) -> None:
        try:
            destinations = classify_destinations(event)
            for destination in destinations:
                artifact = build_artifact(event, destination)
                self._store.append(destination, artifact)
        except Exception as exc:  # noqa: BLE001 -- a recorder failure must NEVER reach the runtime.
            self.internal_errors.append(f"{type(exc).__name__}: {exc}")

    def record_session_start(self, started_at: datetime, initial_state: str) -> None:
        """Called once by ShadowSessionController.start_session() --
        the ONE session-lifecycle touch point this gate's approved
        integration boundary permits beyond passive EventBus
        subscription."""
        try:
            self._store.write_metadata(SessionMetadata(
                session_id=self._store.session_id, started_at=started_at.isoformat(), initial_state=initial_state,
            ))
        except Exception as exc:  # noqa: BLE001
            self.internal_errors.append(f"{type(exc).__name__}: {exc}")

    def record_session_manifest(self, manifest: SessionManifest) -> None:
        """The evidence-chain 'identity card' for this session --
        written once, alongside metadata.json, at session start. Every
        field on `manifest` is caller-supplied; this method reshapes
        nothing and interprets nothing, it only persists what it was
        given."""
        try:
            self._store.write_json_file("session_manifest.json", manifest)
        except Exception as exc:  # noqa: BLE001
            self.internal_errors.append(f"{type(exc).__name__}: {exc}")

    def record_heartbeat_snapshot(self, heartbeat) -> None:
        """Heartbeats are not published on the EventBus (F.5's own
        heartbeat() is a pull-based snapshot, not a domain event) --
        the session controller calls this directly, per the approved
        integration boundary (EventBus -> Observatory for domain
        events, ShadowSessionController -> Observatory for session
        start/end AND heartbeat snapshots, since a heartbeat is not an
        event any producer emits on its own)."""
        try:
            artifact = HeartbeatArtifact(
                time=heartbeat.timestamp.isoformat(), runtime_state=heartbeat.runtime_state,
                positions=heartbeat.active_positions_count, broker_connected=(heartbeat.broker_status == "CONNECTED"),
                market_feed_status=heartbeat.market_feed_status,
                last_decision_time=heartbeat.last_decision_timestamp.isoformat() if heartbeat.last_decision_timestamp else None,
                last_execution_time=heartbeat.last_execution_timestamp.isoformat() if heartbeat.last_execution_timestamp else None,
            )
            self._store.append("heartbeat.jsonl", artifact)
        except Exception as exc:  # noqa: BLE001
            self.internal_errors.append(f"{type(exc).__name__}: {exc}")

    def finalize_session(
        self, final_positions: Tuple[str, ...], realized_pnl: float, unrealized_pnl: Optional[float],
    ) -> None:
        """Called once by ShadowSessionController.run_eod_reconciliation()
        -- generates summary.json purely from the artifact files this
        recorder already wrote (see report_builder.py's own docstring
        for why this is not a duplicate of F.5's in-memory summary)."""
        try:
            summary = build_session_summary(
                self._store, self._store.session_id, final_positions, realized_pnl, unrealized_pnl,
            )
            self._store.write_summary(summary)
        except Exception as exc:  # noqa: BLE001
            self.internal_errors.append(f"{type(exc).__name__}: {exc}")
