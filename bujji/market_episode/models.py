"""Market Episode Engine models — frozen, immutable records.

Implements Deliverable 2 (`Episode`). Every dataclass here is
`frozen=True` and carries no logic -- construction lives in
`engine.py`, never here.

---------------------------------------------------------------------
Design decision — episode_id: fixed at creation, never recomputed.
---------------------------------------------------------------------
`episode_id` is a deterministic hashlib.md5 hash minted ONCE, at
creation time, over `episode_type` plus the ordered tuple of
originating_event_ids AS THEY STOOD AT CREATION (the founding
event(s) only). It never changes across the episode's lifetime.

An Episode GROWS over time (new compatible events extend it), but per
this project's append-only-recording discipline (mirroring Series 75/
74's append-only journal precedent), growth is represented by
producing a NEW, immutable `Episode` snapshot with the SAME
episode_id and a superset `originating_event_ids`/
`originating_observation_ids` tuple, plus an updated `latest_update`
timestamp -- never by mutating a prior `Episode` object in place, and
never by recomputing episode_id from the grown content. If episode_id
were recomputed on every growth, two real properties this design needs
would break: (a) "the same episode, referenced by id, across its whole
life" -- a consumer holding an episode_id from an early snapshot could
no longer find later snapshots of the *same* episode; (b) deterministic
replay parity for a *growing* episode -- growth order interleaves with
other episodes' events in live mode but not necessarily in the same
wall-clock interleaving in batch/replay mode, so hashing "current
content" would make batch and live disagree on an id for the identical
underlying episode. Hashing only the immutable founding content avoids
both problems.

`schema_version` follows Series 75's convention (own field, not
inherited implicitly).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple


# ---------------------------------------------------------------------------
# EpisodeProvenance — mirrors MarketEventProvenance's shape (Series 75),
# applied at the grouping layer rather than the event-detection layer.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class EpisodeProvenance:
    originating_source: str        # e.g. "market_episode.engine" or the caller-supplied grouper name.
    detection_context: str         # e.g. "LIVE", "REPLAY", "BATCH" — how the grouping was run.
    schema_version: str


# ---------------------------------------------------------------------------
# Episode — the canonical unit (Deliverable 2). References MarketEvents
# and Observations by their existing ids only — NEVER copies an event's
# or observation's payload, per this sprint's explicit, repeated
# instruction. `originating_event_ids` and `originating_observation_ids`
# are both tuples that GROW across successive immutable snapshots that
# share the same `episode_id` (see module docstring).
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Episode:
    episode_id: str
    episode_type: str                                # One of taxonomy.ALL_EPISODE_TYPES.
    start_time: str                                   # Founding event's timestamp — fixed at creation, never changes.
    latest_update: str                                # Timestamp of the most recent snapshot (growth or transition).
    end_time: Optional[str]                           # Set only once CLOSED; None otherwise.
    originating_event_ids: Tuple[str, ...]            # Grows across snapshots; never shrinks.
    originating_observation_ids: Tuple[str, ...]      # Transitively collected from originating_event_ids; grows; never shrinks.
    current_state: str                                # One of taxonomy.ALL_EPISODE_STATES.
    provenance: EpisodeProvenance
    schema_version: str
