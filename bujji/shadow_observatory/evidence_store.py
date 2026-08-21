"""Decision evidence: the observations a decision cited, actually persisted.

WHAT WAS WRONG. On 2026-08-20 the live session's decisions cited 346
distinct supporting observation IDs across `supporting_observation_ids`,
`evidence_ids` and `which_observations_support_it`. Not one of them
resolved -- not in the normalized store, not in layer0, nowhere on disk.
The structure of an audit trail was present and populated; the trail
itself did not exist. Meanwhile the campaign report printed "Explanation
completeness: 100%", because that metric measures whether fields are
POPULATED, not whether what they point at is REAL.

The cause was not carelessness. Two pipelines observe the market
independently: the capture session persists what IT polled, and the
decision path builds its own observations from ITS own snapshot, cites
them, and drops them at end of cycle. The IDs were honest identifiers for
facts that were never written down.

WHY REBUILDING WORKS. `market_observation.engine.build_observation()` mints
`observation_id` as a CONTENT HASH -- md5 over identity plus value -- and
nothing in it reads a clock or a random source. Rebuilding the observations
from the SAME snapshot therefore reproduces byte-identical IDs. Verified
empirically before this module was written, and asserted by its tests.

That property is what makes this additive rather than invasive: the
decision path is not touched, not intercepted, and given no sink to write
to. This module re-derives what that path built and persists it. If the
two ever diverge, `unresolved_ids()` reports it instead of hiding it.

WHY EVERY CYCLE, NOT EVERY DECISION. PSI and MSSI cite observations from
across the rolling window, not just the current instant. Persisting only on
cycles that produced a thesis would leave earlier cycles' evidence dangling
-- which is also why only 6 of 66+ cycles left any record on 2026-08-20.
Evidence is written every cycle, whatever the cycle concluded.
"""
from __future__ import annotations

import json
import os
import tempfile
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set

RECORD_TYPE = "DECISION_EVIDENCE"
SCHEMA_VERSION = "1.0.0"


def evidence_records_for_snapshot(snapshot) -> List[Dict[str, Any]]:
    """Rebuild exactly the observations the decision path built from this
    snapshot, serialized whole.

    Never raises: evidence capture must not be able to end a trading
    session. A builder that fails contributes nothing rather than
    propagating -- and its absence then shows up honestly as an
    unresolved ID rather than as a silent gap.
    """
    from bujji.market_observation.serialization import observation_to_dict
    from bujji.market_state_builder.observation_bridge import (
        build_futures_observation_from_snapshot,
        build_observation_from_snapshot,
        build_vix_observation_from_snapshot,
    )

    records: List[Dict[str, Any]] = []

    def _add(role: str, observation) -> None:
        if observation is None:
            return
        records.append({
            "record_type": RECORD_TYPE,
            "schema_version": SCHEMA_VERSION,
            "role": role,
            "observation_id": observation.identity.observation_id,
            "observation": observation_to_dict(observation),
        })

    for role, builder in (
        ("spot", build_observation_from_snapshot),
        ("vix", build_vix_observation_from_snapshot),
        ("futures", build_futures_observation_from_snapshot),
    ):
        try:
            _add(role, builder(snapshot))
        except Exception:  # noqa: BLE001 -- evidence capture never ends a session
            continue

    try:
        from bujji.market_state_builder.option_observation_bridge import (
            build_option_observations_from_snapshot,
        )
        for leg in build_option_observations_from_snapshot(snapshot) or ():
            observation = getattr(leg, "observation", leg)
            if getattr(observation, "identity", None) is not None:
                _add("option_leg", observation)
    except Exception:  # noqa: BLE001
        pass

    return records


def append_evidence(path: str, records: Sequence[Dict[str, Any]]) -> int:
    """Append records not already present. Returns how many were written.

    Deduplicated by observation_id against what the file already holds:
    the same fact observed on two cycles is ONE fact, and a content hash
    says so. Append-only -- a record once written is never rewritten,
    matching the discipline of every other store here.

    Durability: appended and fsynced, so a crash mid-session cannot lose
    evidence for decisions that were already made.
    """
    if not records:
        return 0
    known = set(load_evidence_index(path))
    fresh = []
    for record in records:
        oid = record.get("observation_id")
        if not oid or oid in known:
            continue
        known.add(oid)
        fresh.append(record)
    if not fresh:
        return 0

    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "a", encoding="utf-8") as handle:
        for record in fresh:
            handle.write(json.dumps(record, sort_keys=True, default=str) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    return len(fresh)


def load_evidence_index(path: str) -> Dict[str, Dict[str, Any]]:
    """observation_id -> record, for everything persisted so far.

    A malformed line is skipped rather than raising: one corrupt line must
    not make an entire session's evidence unreadable. Absent file is an
    empty index, not an error -- a session that has recorded nothing yet
    has recorded nothing, which is different from having failed.
    """
    index: Dict[str, Dict[str, Any]] = {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                oid = record.get("observation_id")
                if oid:
                    index[oid] = record
    except FileNotFoundError:
        return {}
    except OSError:
        return {}
    return index


def resolve_evidence(path: str, observation_id: str) -> Optional[Dict[str, Any]]:
    """The persisted observation behind one cited ID, or None if it does
    not resolve. None means exactly that -- the trail is broken here."""
    return load_evidence_index(path).get(observation_id)


def cited_observation_ids(cycle_record: Any) -> Set[str]:
    """Every OBS- id a cycle record cites, wherever it appears.

    Walks the record rather than reading a fixed list of keys: the cited
    ids live under at least nine different paths today
    (`supporting_observation_ids`, `evidence_ids`,
    `which_observations_support_it`, per-lens `supporting_evidence_ids`,
    ...) and a fixed list would silently stop covering a new one -- which
    is precisely how a broken trail stays invisible.

    Only `OBS-` ids are collected. Assessment ids (`MTA-`, `MDA-`) name
    conclusions, not observations, and do not belong to this store.
    """
    found: Set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, str):
            if node.startswith("OBS-"):
                found.add(node)
        elif isinstance(node, dict):
            for value in node.values():
                walk(value)
        elif isinstance(node, (list, tuple, set)):
            for value in node:
                walk(value)

    walk(cycle_record)
    return found


def unresolved_ids(path: str, cycle_record: Any) -> List[str]:
    """Cited ids with nothing behind them, sorted.

    This is the honest counterpart to "Explanation completeness: 100%".
    An empty list means the trail is whole; a non-empty one names exactly
    where it is broken, so the condition is visible instead of silent.
    """
    index = load_evidence_index(path)
    return sorted(oid for oid in cited_observation_ids(cycle_record) if oid not in index)


def evidence_integrity(path: str, cycle_record: Any) -> Dict[str, Any]:
    """The per-cycle integrity block recorded beside the thesis.

    `resolution_rate` is a real fraction of ids that resolve -- not a
    field-presence count. With no ids cited it is None, never 1.0: a
    cycle that cited nothing has not demonstrated a whole trail, and
    scoring it perfect is the exact failure mode this replaces.
    """
    cited = cited_observation_ids(cycle_record)
    unresolved = unresolved_ids(path, cycle_record)
    resolved = len(cited) - len(unresolved)
    return {
        "cited_count": len(cited),
        "resolved_count": resolved,
        "unresolved_count": len(unresolved),
        # Capped at 25 so a wholly broken trail cannot bloat every record;
        # the counts above stay exact regardless.
        "unresolved_ids": unresolved[:25],
        "resolution_rate": (resolved / len(cited)) if cited else None,
        # NOT vacuously True when nothing was cited. `not unresolved` is
        # trivially true for an empty citation set, which would hand a
        # consumer a green light on a cycle that produced no evidence at all
        # -- the identical shape of defect this module exists to remove
        # ("Explanation completeness: 100%" over an empty trail). A cycle
        # that cited nothing has not demonstrated a whole trail; it has
        # demonstrated nothing, and that is recorded as None.
        "all_cited_ids_resolve": (not unresolved) if cited else None,
    }
