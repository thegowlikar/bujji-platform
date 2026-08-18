"""Shadow Runtime Recovery -- Phase 15C.

Additive integration of Phase 15B's state-persistence layer into
ShadowSessionRunner startup. Off by default: `ShadowSessionRunner`'s
new `regime_memory_event_store_path` constructor parameter defaults to
`None`, and with it unset this module is never even imported at
runtime by the runner -- a runner built exactly as before behaves
exactly as before, byte for byte.

Cycle identity: `next_cycle_index` is derived from an ALREADY REAL
number -- how many cycles this session has already actually persisted
to its own `intelligence_cycle_path` JSONL (one line per real,
already-observed cycle). This is not invented: it is simply resuming
the count of real market observations that already happened. Combined
with `EventStore`'s own (session_id, cycle_id) deterministic dedup, a
cycle_id can never collide with, or cause a replay of, an
already-persisted real cycle -- even if this count were ever off by a
little, the store's own idempotent-append guarantee is the real safety
net, not this counter.
"""
from __future__ import annotations

import os
from typing import Optional, Tuple

from bujji.market_regime_memory.models import RegimeMemoryState
from bujji.state_persistence.models import RecoveryReport
from bujji.state_persistence.regime_memory import hydrate_regime_memory
from bujji.state_persistence.store import EventStore


def count_persisted_cycles(intelligence_cycle_path: Optional[str]) -> int:
    """Zero for a fresh session (file doesn't exist yet) -- never
    guessed, just a real line count of already-recorded real cycles."""
    if not intelligence_cycle_path or not os.path.exists(intelligence_cycle_path):
        return 0
    with open(intelligence_cycle_path) as f:
        return sum(1 for _ in f)


def recover_shadow_session(
    intelligence_cycle_path: Optional[str],
    regime_memory_event_store_path: Optional[str],
    session_id: str,
) -> Tuple[RegimeMemoryState, int, Optional[RecoveryReport]]:
    """Returns (initial_regime_state, next_cycle_index, recovery_report).

    `recovery_report` is `None` only when `regime_memory_event_store_path`
    was not supplied at all (recovery not requested for this run) --
    kept distinct from a real `RECOVERY_COMPLETE` with `events_discovered=0`
    (recovery WAS requested, and there genuinely was nothing to recover,
    e.g. a brand-new session) so a caller can tell "recovery wasn't
    attempted" apart from "recovery ran and found a clean slate"."""
    next_cycle_index = count_persisted_cycles(intelligence_cycle_path)
    if not regime_memory_event_store_path:
        return RegimeMemoryState(), next_cycle_index, None
    store = EventStore(regime_memory_event_store_path)
    state, report = hydrate_regime_memory(store, session_id)
    return state, next_cycle_index, report
