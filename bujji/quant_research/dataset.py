"""Event-ordered, phase-filtered, look-ahead-free reading of tick corpora.

THE ONE RULE THIS FILE ENFORCES: a feature computed at time t may see records
with recv_ts <= t and nothing else. Every convenience that would break it --
random access, whole-file sorting into memory then indexing forward, "just
peek at the next tick to get the exit price" -- is absent by construction
rather than by discipline.

TICK FACTS AND CHAIN FACTS ARE NOT SYNCHRONOUS. A tick carries a receiver
timestamp; a REST chain snapshot carries a fetch time. They are different
observations of different things at different moments, and this module refuses
to place them on one timeline. Correlating them in analysis is legitimate;
presenting them as simultaneous is not.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence

from .manifest import PHASE_VALID, DatasetManifest, verify_manifest

# Provenance labels. Kept distinct so a feature can never claim a source it
# does not have.
SOURCE_TICK = "TICK"
SOURCE_REST_CHAIN = "REST_CHAIN_SNAPSHOT"


class LookAheadError(RuntimeError):
    """Raised when code asks for data later than the point it claims to be at."""


class DatasetRefused(RuntimeError):
    """Raised when a dataset cannot establish its identity or classification."""


@dataclass(frozen=True)
class TickRecord:
    """One market-data callback, as received. Never mutated."""
    seq: Optional[int]
    recv_ts: Optional[float]
    symbol: Optional[str]
    payload: Dict[str, Any]
    source: str = SOURCE_TICK

    def field(self, name: str):
        """A field, or None. NEVER a substituted zero.

        Absence is a fact about the feed; a zero is a fact about the market.
        Conflating them is the defect that produced a fabricated open interest
        earlier in this project.
        """
        return self.payload.get(name)


class TickDataset:
    """Streaming, event-ordered access to one or more tick corpora."""

    def __init__(self, manifest: DatasetManifest, *, verify: bool = True):
        self.manifest = manifest
        refusals = verify_manifest(manifest, recompute=verify)
        if refusals:
            raise DatasetRefused(
                "dataset refused: "
                + "; ".join(f"{r['code']}: {r['detail']}" for r in refusals))
        self._included = set(manifest.included_classifications)
        self._cursor: Optional[float] = None

    # ---- phase filtering -------------------------------------------------
    def classification_at(self, ts: Optional[float]) -> Optional[str]:
        for p in self.manifest.phases:
            if p.contains(ts):
                return p.classification
        return None

    def _included_at(self, ts: Optional[float]) -> bool:
        c = self.classification_at(ts)
        if c is None:
            # UNCLASSIFIED IS EXCLUDED, not assumed valid. A record outside
            # every declared window is a record nobody classified.
            return False
        return c in self._included

    # ---- the only way in ------------------------------------------------
    def stream(self, *, until_ts: Optional[float] = None) -> Iterator[TickRecord]:
        """Yield records in event-time order, excluding non-included phases.

        `until_ts` is inclusive and is how a caller declares the moment it is
        standing at. Records after it are never yielded, so a feature cannot
        accidentally consume its own future.
        """
        for path in sorted(self.manifest.sources):
            for rec in self._read(Path(path)):
                ts = rec.recv_ts
                if until_ts is not None and ts is not None and ts > until_ts:
                    continue
                if not self._included_at(ts):
                    continue
                self._cursor = ts if ts is not None else self._cursor
                yield rec

    def _read(self, path: Path) -> Iterator[TickRecord]:
        last_seq = None
        with open(path, "r", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                p = r.get("payload")
                if not isinstance(p, dict):
                    continue
                seq = r.get("seq")
                # The corpus is append-only and sequence-assigned at the
                # callback, so file order IS event order. Assert rather than
                # assume: an out-of-order file would silently let a later
                # record inform an earlier decision.
                if (isinstance(seq, int) and isinstance(last_seq, int)
                        and seq < last_seq):
                    raise LookAheadError(
                        f"{path.name}: sequence went backwards ({last_seq} -> "
                        f"{seq}); event order cannot be trusted")
                last_seq = seq if isinstance(seq, int) else last_seq
                yield TickRecord(seq=seq, recv_ts=r.get("recv_ts"),
                                 symbol=p.get("symbol"), payload=p)

    def at(self, ts: float) -> "PointInTime":
        """A read-only view standing at `ts`. Cannot see past it."""
        return PointInTime(self, ts)


class PointInTime:
    """A view of the dataset as of one instant.

    Exists so that "what did we know then?" is answerable structurally. A
    caller holding one of these cannot reach later data, because the only
    accessor it exposes filters on its own timestamp.
    """

    def __init__(self, dataset: TickDataset, ts: float):
        self._ds = dataset
        self._ts = ts

    @property
    def now(self) -> float:
        return self._ts

    def history(self) -> Iterator[TickRecord]:
        return self._ds.stream(until_ts=self._ts)

    def future(self, *_args, **_kwargs):
        raise LookAheadError(
            "a point-in-time view has no future. If an evaluation needs a "
            "forward outcome it must be supplied by the labelling step, "
            "explicitly and separately -- never read through the view that "
            "represents what was known at decision time.")


def label_forward_outcome(dataset: TickDataset, symbol: str,
                          decision_ts: float, horizon_s: float,
                          field: str = "ltp") -> Dict[str, Any]:
    """Compute a forward outcome for LABELLING, never for features.

    Separated deliberately and named for what it is. Labels legitimately look
    forward; features may not. Keeping them in one function that anything can
    call is how leakage happens, so this returns a labelled record that carries
    its own horizon and refuses to be mistaken for a feature.
    """
    start = end = None
    for rec in dataset.stream():
        if rec.symbol != symbol or rec.recv_ts is None:
            continue
        v = rec.field(field)
        if v is None:
            continue
        if rec.recv_ts <= decision_ts:
            start = (rec.recv_ts, v)
        elif rec.recv_ts <= decision_ts + horizon_s:
            end = (rec.recv_ts, v)
        else:
            break
    return {
        "kind": "LABEL",
        "symbol": symbol,
        "decision_ts": decision_ts,
        "horizon_s": horizon_s,
        "value_at_decision": start[1] if start else None,
        "value_at_horizon": end[1] if end else None,
        "observed": bool(start and end),
        "warning": ("This is a LABEL and looks forward by construction. It "
                    "must never be used as a feature input."),
    }
