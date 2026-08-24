"""The tick journal: every arrival, in order, exactly as it came off the wire.

WHY THIS IS NOT LAYER 0, and why that is not duplicate authority.

`market_reality.store` already exists, is reachable, and already reserves
`KIND_MARKET_TICK` -- and nothing has ever produced one. It would be the
obvious home, and it is the wrong one, for a reason its own docstring states:

    "an identical id means an identical fact. Re-capturing the same fact is
     normal (a retried poll, a reconnecting feed) ... Nothing is written, and
     nothing is lost."

That is correct for OBSERVATIONS and fatal for TICKS. Layer 0 is
content-addressed: it owns the SET of distinct facts observed, and collapsing a
repeat is the point. A tick stream is not a set of facts, it is a SEQUENCE of
ARRIVALS -- and lite-mode payloads carry only `symbol`, `ltp` and `type` with
no exchange timestamp, so a quiet symbol emits byte-identical ticks. Collapsing
those destroys exactly what this journal exists to measure: rate, gaps, order.

So the two own different evidence with different identity models, and neither
is authoritative over the other's question:

    Layer 0        the set of distinct facts     content-addressed, dedupes
    tick journal   the sequence of arrivals      append-only, never dedupes

WHAT IS RECORDED. The payload VERBATIM, before any field is read from it, plus
two things the payload cannot supply: our own receive time and a monotonically
increasing ingest sequence. No broker sequence number is claimed, because the
SDK does not expose one -- inventing one would be a fabricated ordering.

LOSS IS RECORDED, NEVER HIDDEN. The queue is bounded, because an unbounded one
turns a feed burst into an out-of-memory kill. When it is full the record is
dropped and COUNTED, and the count travels with the journal in its manifest --
a journal with drops is INCOMPLETE, and a replay of it must not present itself
as a faithful reproduction.
"""
from __future__ import annotations

import hashlib
import json
import os
import queue
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Optional

SCHEMA_VERSION = "tick-journal/1"

# A burst that outruns the writer must cost records, not the process. 500k
# records at roughly 200 bytes is ~100 MB of queue in the worst case.
DEFAULT_MAX_QUEUE = 500_000

# Crash-safety policy. Per-record fsync would dominate at the certified rate
# (~2.4 ticks/s/symbol across a 248-symbol universe); never syncing until close
# loses the whole session to a kill -9. Periodic sync bounds the loss window,
# and the bound is RECORDED in the manifest so a reader knows what a crash
# could have cost rather than having to guess.
DEFAULT_FSYNC_EVERY_RECORDS = 500
DEFAULT_FSYNC_EVERY_SECONDS = 2.0


@dataclass(frozen=True)
class JournalStats:
    offered: int
    written: int
    dropped: int
    max_queue_depth: int
    bytes_written: int

    @property
    def complete(self) -> bool:
        """A journal that dropped anything is not a faithful record."""
        return self.dropped == 0 and self.offered == self.written

    def as_dict(self) -> Dict[str, Any]:
        return {
            "offered": self.offered,
            "written": self.written,
            "dropped": self.dropped,
            "max_queue_depth": self.max_queue_depth,
            "bytes_written": self.bytes_written,
            "complete": self.complete,
            # Conservation. If this is ever False the accounting itself is
            # broken, and no number above can be trusted.
            "accounted": self.offered == self.written + self.dropped,
        }


class TickJournal:
    """Append-only, order-preserving, never de-duplicating.

    Thread-safe by construction: `offer()` is called from the SDK's callback
    thread and must never block it, so it does one bounded put and returns.
    All I/O happens on the writer thread.
    """

    def __init__(self, path: Path, *, session_id: str,
                 max_queue: int = DEFAULT_MAX_QUEUE,
                 fsync_every_records: int = DEFAULT_FSYNC_EVERY_RECORDS,
                 fsync_every_seconds: float = DEFAULT_FSYNC_EVERY_SECONDS,
                 clock: Optional[Callable[[], float]] = None,
                 logger=None) -> None:
        import logging

        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.session_id = session_id
        self._logger = logger or logging.getLogger("bujji.tick_journal")
        self._clock = clock or time.time
        self._fsync_every_records = int(fsync_every_records)
        self._fsync_every_seconds = float(fsync_every_seconds)

        self._file = open(self.path, "a", buffering=1024 * 1024, encoding="utf-8")
        self._queue: "queue.Queue[str]" = queue.Queue(maxsize=int(max_queue))
        self._stop = threading.Event()
        self._lock = threading.Lock()

        self._seq = 0
        self._offered = 0
        self._written = 0
        self._dropped = 0
        self._max_depth = 0
        self._bytes = 0
        self._digest = hashlib.sha256()
        self._since_sync = 0
        self._last_sync = self._clock()

        self._thread = threading.Thread(target=self._drain, name="tick-journal",
                                        daemon=True)
        self._thread.start()

    # -- the callback boundary ------------------------------------------ #
    def offer(self, payload: Dict[str, Any]) -> None:
        """Record one arrival. Called from the SDK callback thread.

        NEVER BLOCKS AND NEVER RAISES. A journal that can stall the feed
        thread is worse than no journal: it would turn a disk hiccup into a
        missed tick on the live path. A journal that can raise there would
        take the feed down entirely.

        The sequence number is assigned HERE, not on the writer thread, so it
        reflects arrival order rather than drain order -- which is the whole
        point of recording it.
        """
        try:
            with self._lock:
                self._seq += 1
                self._offered += 1
                seq = self._seq
                record = {
                    "v": SCHEMA_VERSION,
                    "seq": seq,
                    "recv_epoch": self._clock(),
                    "recv_monotonic": time.monotonic(),
                    # VERBATIM. Not a projection, not normalised, not filtered.
                    # Written before anything reads a field from it.
                    "payload": payload,
                }
            line = json.dumps(record, separators=(",", ":"), default=str) + "\n"
        except Exception:  # noqa: BLE001 -- an unserialisable payload is a drop, not a crash
            with self._lock:
                self._dropped += 1
            return

        try:
            self._queue.put_nowait(line)
        except queue.Full:
            with self._lock:
                self._dropped += 1
            return

        depth = self._queue.qsize()
        if depth > self._max_depth:
            with self._lock:
                self._max_depth = max(self._max_depth, depth)

    # -- writer thread --------------------------------------------------- #
    def _drain(self) -> None:
        while not self._stop.is_set() or not self._queue.empty():
            try:
                line = self._queue.get(timeout=0.2)
            except queue.Empty:
                self._maybe_sync(force_time_only=True)
                continue
            try:
                self._file.write(line)
            except Exception as exc:  # noqa: BLE001
                with self._lock:
                    self._dropped += 1
                self._logger.critical(
                    "TICK JOURNAL write failed (%s: %s) -- the record is LOST and "
                    "counted; this journal is no longer complete.",
                    type(exc).__name__, exc)
                continue
            encoded = line.encode("utf-8")
            with self._lock:
                self._written += 1
                self._bytes += len(encoded)
                self._digest.update(encoded)
                self._since_sync += 1
            self._maybe_sync()

    def _maybe_sync(self, force_time_only: bool = False) -> None:
        now = self._clock()
        due_records = (not force_time_only
                       and self._since_sync >= self._fsync_every_records)
        due_time = (self._since_sync > 0
                    and (now - self._last_sync) >= self._fsync_every_seconds)
        if not (due_records or due_time):
            return
        try:
            self._file.flush()
            os.fsync(self._file.fileno())
        except Exception as exc:  # noqa: BLE001 -- a failed sync is not a lost record, but it IS a widened loss window
            self._logger.warning("TICK JOURNAL fsync failed (%s: %s)",
                                 type(exc).__name__, exc)
            return
        with self._lock:
            self._since_sync = 0
        self._last_sync = now

    # -- accounting ------------------------------------------------------ #
    def stats(self) -> JournalStats:
        with self._lock:
            return JournalStats(
                offered=self._offered, written=self._written, dropped=self._dropped,
                max_queue_depth=self._max_depth, bytes_written=self._bytes)

    def content_sha256(self) -> str:
        with self._lock:
            return self._digest.hexdigest()

    def close(self, timeout: float = 30.0) -> JournalStats:
        """Drain, sync, and return the final accounting. Idempotent."""
        self._stop.set()
        self._thread.join(timeout=timeout)
        try:
            self._file.flush()
            os.fsync(self._file.fileno())
            self._file.close()
        except Exception as exc:  # noqa: BLE001
            self._logger.warning("TICK JOURNAL close failed (%s: %s)",
                                 type(exc).__name__, exc)
        stats = self.stats()
        if not stats.complete:
            self._logger.critical(
                "TICK JOURNAL INCOMPLETE: offered=%d written=%d dropped=%d. "
                "A replay of this journal is NOT a faithful reproduction of the "
                "session, and must not be presented as one.",
                stats.offered, stats.written, stats.dropped)
        return stats
