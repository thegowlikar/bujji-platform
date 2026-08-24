"""Gate 1 — LIVE FEED CAPABILITY MEASUREMENT (v5).

v4 lives OUTSIDE the live checkout (/opt/bujji/gate1-run) and imports the
application read-only. The checkout at /opt/bujji/app is not modified by this
run. v4 = v3 plus four measurement corrections, all found while preparing the
session and all in the reporting path, none in the feed path:

  C1 RECONNECT RESTORATION was unmeasurable. `symbols_missing` was computed as
     `100 - len(busiest_symbols)`, and busiest_symbols is most_common(10) --
     so it reported 90 missing whenever ANY message arrived, and could never
     show full restoration. It now compares the post-reconnect symbol set
     against the set subscribed, which is the actual question: did every
     subscription come back, or only some of them?
  C2 TIME-TO-FIRST-TICK was `round(s["duration_s"] and (t_sub and 0) or 0, 3)`,
     which evaluates to 0 unconditionally. Now measured from resubscribe.
  C3 FREE-SPACE TRAJECTORY was a threshold guard only (abort below 2 GB). A
     trajectory is now sampled through the run, so the report can say whether
     a full session would have fitted, not merely that this one did.
  C4 SESSION MANIFEST + SEALING. The run now records its own provenance (host,
     checkout SHA, universe digest, harness digest, credential IDENTITY never
     value) and seals every artifact with a sha256. Unsealed evidence is a
     Gate 1 failure, so the seal has to exist to be checked.

  And one grading change: the run ends with an explicit PASS/FAIL verdict
  against the stated Gate 1 failure conditions, instead of printed warnings a
  reader has to assemble themselves.

Original v3 header follows.

Gate 1 — LIVE FEED CAPABILITY MEASUREMENT (v3).

READ-ONLY WITH RESPECT TO TRADING. No orders, no broker write path, no
imports from execution / strategy / risk / lifecycle / memory.

Binds `data_ws.FyersDataSocket` DIRECTLY: `FyersTickFeed` keeps only
`symbol`+`ltp` and discards the other 21 full-mode fields
(bujji/broker/fyers_ws.py:312-321). Gate 1 measures the FEED, not the
wrapper.

MEASURES REALITY ONLY. Chooses no TickStore, storage tier, watermark,
ingestion topology, aggregation policy or universe change.

v3 corrections (readiness review):
  D1 lock around Metrics + per-message thread id (G8)
  D2 bounded duplicate window (was unbounded -> OOM risk)
  D3 depth phase isolated (SymbolUpdate unsubscribed first)
  D4 MAX_DISK_BYTES actually enforced; dead imports removed
  G1 LATENESS MAGNITUDE distribution  <-- the watermark input
  G2 sustained full-universe phase with per-minute buckets
  G3 CPU / RSS / thread sampling (dependency-free, /proc)
  G4 subscription-ack correlation per symbol
  G5 offline raw-corpus re-parse validator
  G6 burst recovery / queue drain timing
  G7 optional ceiling probe beyond the locked universe
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import queue
import shutil
import statistics
import threading
import time
from collections import Counter, deque, defaultdict
from datetime import datetime, timezone
from pathlib import Path

def derive_ramp(n: int) -> list:
    """Rungs from the ACTUAL universe length, never a stale hardcoded list.

    v4 shipped `RAMP = [10, 50, 100, 200, 300, 411]`, whose top rung came from
    the retired 410-symbol universe model. Against the canonical universe that
    rung asks for more symbols than exist, and every rung below it is a
    coincidence rather than a fraction of the thing being measured. Rungs are
    now fractions of the real N, so the ramp means the same thing whatever the
    universe turns out to be.
    """
    if n <= 0:
        raise SystemExit("universe is empty -- nothing to ramp")
    rungs = sorted({max(1, int(n * f)) for f in (0.02, 0.1, 0.25, 0.5, 0.75)} | {n})
    return [r for r in rungs if r <= n]
QUEUE_MAX = 500_000
DUP_WINDOW = 250_000            # D2: bounded duplicate-detection window
MAX_DISK_BYTES = 20 * 1024**3   # D4: enforced below
MIN_FREE_BYTES = 2 * 1024**3

# TERMINAL STATES, mutually exclusive and machine-readable.
#
# A run that stopped early and a run that finished are different facts, and
# on 2026-08-24 they were indistinguishable: the harness had no signal
# handler, no finally and no atexit, so an operator-terminated run produced
# NO results.json at all -- no offline re-parse, no accounting identity, no
# verdict. 394 MB of good evidence sat on disk with nothing certifying it.
TERMINAL_COMPLETED = "COMPLETED"
TERMINAL_DEADLINE = "DEADLINE_REACHED"
TERMINAL_INTERRUPTED = "INTERRUPTED"
TERMINAL_ERROR = "ERROR"

# How often a long wait re-checks the absolute deadline. run_phase used to
# call guard() once at phase entry and then time.sleep(seconds) -- an
# 18,000-second uninterruptible sleep for the sustained phase. The deadline
# could not fire mid-phase, so a 15:35 stop ran until 20:26.
DEADLINE_POLL_S = 5
MAX_RUNTIME_S = 8 * 3600


# --------------------------------------------------------------------- proc
def proc_sample() -> dict:
    """G3: CPU/RSS/threads without psutil. Linux /proc only."""
    out = {"rss_bytes": None, "threads": None, "utime_s": None, "stime_s": None}
    try:
        for line in open("/proc/self/status"):
            if line.startswith("VmRSS:"):
                out["rss_bytes"] = int(line.split()[1]) * 1024
            elif line.startswith("Threads:"):
                out["threads"] = int(line.split()[1])
        parts = open("/proc/self/stat").read().split()
        ticks = os.sysconf("SC_CLK_TCK")
        out["utime_s"] = int(parts[13]) / ticks
        out["stime_s"] = int(parts[14]) / ticks
    except Exception:
        pass
    return out


# ------------------------------------------------------------------- corpus
class RawCorpus:
    """Append-only verbatim wire record, written BEFORE any derivation.
    A full queue is real data loss: counted, never hidden."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._opened_at = time.time()
        self._f = open(path, "a", buffering=4 * 1024 * 1024)
        self._q: queue.Queue = queue.Queue(maxsize=QUEUE_MAX)
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self.offered = 0
        self.written = 0
        self.dropped = 0
        # REJECTED is not DROPPED. Dropped = the queue was full, so the feed
        # delivered something we did not keep. Rejected = we accepted it and
        # the WRITE failed. Both are ingestion loss and both fail Gate 1, but
        # collapsing them would hide which half of the path broke.
        self.rejected = 0
        self.write_errors: list = []
        # Records that could not be frozen at the callback. Distinct from a
        # write failure: nothing ever reached the queue.
        self.snapshot_failures: list = []
        # Size of the most recently frozen line, so callers report the size of
        # what was actually STORED rather than re-serialising to measure it.
        self.last_line_size = 0
        self.max_qdepth = 0
        self.depth_samples: list[tuple[float, int]] = []   # G6
        self._t = threading.Thread(target=self._drain, daemon=True)
        self._t.start()
        self._mon = threading.Thread(target=self._monitor, daemon=True)
        self._mon.start()

    def offer(self, *, seq: int, recv_wall: float, recv_mono: float,
              tid: int, payload) -> None:
        """SNAPSHOT ON THE CALLBACK THREAD, then queue the snapshot.

        WHY THIS IS NOT AN OPTIMISATION. The previous version queued the SDK's
        payload DICT by reference and serialised it later on the writer thread.
        Between those two moments the SDK owns that object and may reuse or
        mutate it, so what reached the file was not provably what arrived --
        which is the entire claim a raw corpus exists to support. The record is
        now frozen to an immutable string here, in the callback, before the
        object is handed to anyone.

        NEVER BLOCKS, NEVER RAISES. A corpus that can stall this thread turns a
        disk hiccup into a missed tick; one that can raise takes the feed down.
        """
        with self._lock:
            self.offered += 1
        try:
            line = json.dumps(
                {"seq": seq, "recv_ts": recv_wall, "recv_monotonic": recv_mono,
                 "tid": tid, "payload": payload},
                separators=(",", ":"), default=str) + "\n"
        except Exception as exc:  # noqa: BLE001
            # A record we cannot freeze is a record we cannot honestly claim to
            # have captured. Counted as REJECTED (it fails Gate 1), never
            # silently omitted -- an omission would leave a sequence hole that
            # replay would read as loss with no explanation.
            with self._lock:
                self.rejected += 1
                if len(self.snapshot_failures) < 50:
                    self.snapshot_failures.append(
                        {"seq": seq, "recv_ts": recv_wall,
                         "category": type(exc).__name__,
                         "detail": str(exc)[:200]})
            return
        self.last_line_size = len(line)
        try:
            self._q.put_nowait(line)
        except queue.Full:
            with self._lock:
                self.dropped += 1

    def _monitor(self) -> None:
        """G6: sample queue depth on a timer so burst build-up and drain
        time are observable, not inferred."""
        while not self._stop.is_set():
            d = self._q.qsize()
            if d > self.max_qdepth:
                self.max_qdepth = d
            self.depth_samples.append((time.time(), d))
            time.sleep(0.5)

    def _drain(self) -> None:
        while not self._stop.is_set() or not self._q.empty():
            try:
                rec = self._q.get(timeout=0.25)
            except queue.Empty:
                continue
            try:
                # ALREADY A STRING. The writer never serialises an SDK object,
                # so nothing it writes can have changed since it arrived.
                self._f.write(rec)
            except Exception as exc:  # noqa: BLE001 -- a lost write is evidence
                with self._lock:
                    self.rejected += 1
                    if len(self.write_errors) < 50:
                        self.write_errors.append(
                            {"t": time.time(), "err": f"{type(exc).__name__}: {exc}"})
                continue
            self.written += 1

    @property
    def qdepth(self) -> int:
        return self._q.qsize()

    def close(self) -> None:
        self._stop.set()
        self._t.join(timeout=180)
        self._mon.join(timeout=5)
        self._f.flush()
        os.fsync(self._f.fileno())
        self._f.close()

    def burst_recovery(self) -> dict:
        """G6: longest span the queue stayed above 10% of capacity, and
        how long it took to return to near-empty afterwards."""
        thresh = QUEUE_MAX * 0.10
        peaks, cur = [], None
        for ts, d in self.depth_samples:
            if d > thresh and cur is None:
                cur = ts
            elif d <= thresh * 0.05 and cur is not None:
                peaks.append(round(ts - cur, 2))
                cur = None
        return {"burst_episodes": len(peaks),
                "longest_recovery_s": max(peaks) if peaks else 0.0,
                "samples": len(self.depth_samples)}

    def accounting(self) -> dict:
        return {
            "offered": self.offered, "written": self.written, "dropped": self.dropped,
            "rejected": self.rejected, "write_errors": list(self.write_errors),
            "snapshot_failures": list(self.snapshot_failures),
            "rejected_breakdown": {
                "snapshot_failures": len(self.snapshot_failures),
                "write_failures": self.rejected - len(self.snapshot_failures)},
            "drop_pct": round(100.0 * self.dropped / self.offered, 4) if self.offered else 0.0,
            "max_queue_depth": self.max_qdepth, "queue_capacity": QUEUE_MAX,
            # THE ACCOUNTING IDENTITY. Every message the callback offered must
            # be exactly one of written, rejected, dropped, or STILL IN FLIGHT.
            #
            # In-flight is explicit because omitting it made the identity read
            # as BROKEN whenever it was evaluated mid-run with records still
            # queued -- a benign state indistinguishable, in the report, from
            # real silent loss. After close() the queue is drained and
            # in_flight is 0, so the strict form still applies at the only
            # moment that certifies a run.
            "in_flight": self._q.qsize(),
            "accounted": (self.offered == self.written + self.rejected
                          + self.dropped + self._q.qsize()),
            "accounting_identity":
                "offered == written + rejected + dropped + in_flight",
            "settled": (self.offered == self.written + self.rejected + self.dropped),
            "unaccounted": self.offered - (self.written + self.rejected
                                           + self.dropped + self._q.qsize()),
            "raw_bytes": self.path.stat().st_size if self.path.exists() else 0,
            "write_throughput_bytes_per_s": (
                round((self.path.stat().st_size if self.path.exists() else 0)
                      / (time.time() - self._opened_at), 1)
                if time.time() > self._opened_at else None),
            "write_throughput_msgs_per_s": (
                round(self.written / (time.time() - self._opened_at), 2)
                if time.time() > self._opened_at else None),
            "burst_recovery": self.burst_recovery(),
        }


# ------------------------------------------------------------------- stats
def pctl(xs, p):
    if not xs:
        return None
    s = sorted(xs)
    return round(s[min(int(len(s) * p), len(s) - 1)], 3)


def dist(xs) -> dict:
    """Full distribution. NEGATIVE VALUES PRESERVED EXACTLY — never clamped."""
    if not xs:
        return {"n": 0}
    return {"n": len(xs), "min": round(min(xs), 3), "max": round(max(xs), 3),
            "p50": pctl(xs, .50), "p90": pctl(xs, .90), "p95": pctl(xs, .95),
            "p99": pctl(xs, .99), "p999": pctl(xs, .999),
            "mean": round(statistics.fmean(xs), 3),
            "negative_count": sum(1 for x in xs if x < 0)}


class Metrics:
    """D1: every mutation happens under `self.lock`."""

    def __init__(self, kind_of: dict):
        self.lock = threading.Lock()
        self.kind_of = kind_of
        self.field_present, self.field_null = Counter(), Counter()
        self.per_symbol, self.per_kind, self.msg_types = Counter(), Counter(), Counter()
        self.thread_ids = Counter()                      # G8
        self.exch_minus_recv_ms: list[float] = []
        self.recv_to_proc_ms: list[float] = []
        self.lateness_ms: list[float] = []               # G1  <-- watermark input
        self.lateness_by_kind = defaultdict(list)        # G1
        self.sizes: list[int] = []
        self.first = self.last = None
        self.last_recv: dict[str, float] = {}
        self.max_exch: dict[str, float] = {}
        self.gaps = defaultdict(list)
        self.gaps_by_kind = defaultdict(list)
        self.exch_nonmonotonic = self.recv_nonmonotonic = 0
        self.exact_duplicates = 0
        self._seen: set = set()
        self._seen_fifo: deque = deque(maxlen=DUP_WINDOW)   # D2
        self.per_second = Counter()
        self.per_minute_msgs = Counter()                 # G2
        self.per_minute_bytes = Counter()                # G2
        self.total = 0

    def observe(self, p: dict, recv: float, proc: float, size: int, tid: int,
                recv_mono: float = None, proc_mono: float = None) -> None:
        """`recv`/`proc` are WALL clock, for correlating with external events.
        `recv_mono`/`proc_mono` are MONOTONIC, and every duration below is
        computed from them.

        WHY BOTH. Wall clock can step -- NTP correction, a VM resuming -- and a
        step lands in the interarrival distribution as a gap that never
        happened, or hides one that did. Monotonic time cannot step, but it has
        no meaning outside this process, so it cannot be lined up against a log
        line or an exchange timestamp. Neither alone is sufficient; the fix is
        to record both and use each for what only it can do.
        """
        if recv_mono is None:
            recv_mono = recv
        if proc_mono is None:
            proc_mono = proc
        with self.lock:
            self.total += 1
            self.sizes.append(size)
            self.thread_ids[tid] += 1
            if self.first is None:
                self.first = recv
            self.last = recv
            self.per_second[int(recv)] += 1
            minute = int(recv // 60)
            self.per_minute_msgs[minute] += 1
            self.per_minute_bytes[minute] += size
            self.recv_to_proc_ms.append((proc_mono - recv_mono) * 1000.0)

            for k, v in p.items():
                (self.field_present if v is not None else self.field_null)[k] += 1

            sym = p.get("symbol", "<none>")
            kind = self.kind_of.get(sym, "UNKNOWN")
            self.per_symbol[sym] += 1
            self.per_kind[kind] += 1
            self.msg_types[str(p.get("type"))] += 1

            sig = (sym, p.get("ltp"), p.get("exch_feed_time"), p.get("last_traded_time"))
            if sig in self._seen:
                self.exact_duplicates += 1
            else:
                if len(self._seen_fifo) == DUP_WINDOW:
                    self._seen.discard(self._seen_fifo[0])
                self._seen_fifo.append(sig)
                self._seen.add(sig)

            # GAPS FROM MONOTONIC TIME. A wall-clock step would otherwise
            # appear as a spurious gap (forward) or as negative time
            # (backward), and both would be reported as feed behaviour.
            prev_r = self.last_recv.get(sym)
            if prev_r is not None:
                g = recv_mono - prev_r
                self.gaps[sym].append(g)
                self.gaps_by_kind[kind].append(g)
                if recv_mono < prev_r:
                    self.recv_nonmonotonic += 1
            self.last_recv[sym] = recv_mono

            e = p.get("exch_feed_time")
            if isinstance(e, (int, float)) and e > 1_000_000_000:
                e = float(e)
                prev_max = self.max_exch.get(sym)
                if prev_max is not None and e < prev_max:
                    # G1: HOW LATE, not merely that it was late.
                    self.exch_nonmonotonic += 1
                    late = (prev_max - e) * 1000.0
                    self.lateness_ms.append(late)
                    self.lateness_by_kind[kind].append(late)
                else:
                    self.max_exch[sym] = e
                self.exch_minus_recv_ms.append((recv - e) * 1000.0)

    def summary(self, subscribed: list[str], acks: dict) -> dict:
        with self.lock:
            dur = (self.last - self.first) if (self.first and self.last) else 0.0
            raw = list(self.exch_minus_recv_ms)
            floor = min(raw) if raw else None
            jitter = [x - floor for x in raw] if floor is not None else []
            rates = list(self.per_second.values())
            per_min = sorted(self.per_minute_msgs.items())
            return {
                "messages": self.total,
                "duration_s": round(dur, 2),
                "msgs_per_sec": round(self.total / dur, 3) if dur > 0 else None,
                "msgs_per_sec_per_instrument": round(self.total / dur / len(subscribed), 5)
                                                if dur > 0 and subscribed else None,
                "bytes_total": sum(self.sizes),
                "bytes_per_sec": round(sum(self.sizes) / dur, 1) if dur > 0 else None,
                "bytes_per_msg": dist([float(s) for s in self.sizes]),
                "per_minute_msgs": [{"minute": m, "msgs": c,
                                      "bytes": self.per_minute_bytes[m]} for m, c in per_min],
                "burst_msgs_per_sec": {"max": max(rates) if rates else 0,
                                        "p95": pctl([float(r) for r in rates], .95),
                                        "median": statistics.median(rates) if rates else 0,
                                        "quiet_seconds": sum(1 for r in rates if r == 0)},
                "by_instrument_kind": dict(self.per_kind),
                "msg_types": dict(self.msg_types),
                "callback_thread_ids": dict(self.thread_ids),
                "FIELD_ENVELOPE_present": dict(self.field_present.most_common()),
                "FIELD_ENVELOPE_null": dict(self.field_null.most_common()),
                "raw_exch_to_recv_ms": dist(raw),
                "clock_offset_floor_ms": round(floor, 3) if floor is not None else None,
                "transport_jitter_above_floor_ms": dist(jitter),
                "recv_to_processing_ms": dist(self.recv_to_proc_ms),
                "LATENESS_MAGNITUDE_ms": dist(self.lateness_ms),
                "LATENESS_MAGNITUDE_by_kind": {k: dist(v) for k, v in self.lateness_by_kind.items()},
                "interarrival_s": dist([g for v in self.gaps.values() for g in v]),
                "interarrival_by_kind": {k: dist(v) for k, v in self.gaps_by_kind.items()},
                "exch_time_nonmonotonic": self.exch_nonmonotonic,
                "recv_time_nonmonotonic": self.recv_nonmonotonic,
                "gap_clock": "monotonic",
                "correlation_clock": "wall",
                "exact_duplicate_msgs": self.exact_duplicates,
                "dup_window": DUP_WINDOW,
                "busiest_symbols": self.per_symbol.most_common(10),
                # The FULL per-symbol map. busiest_symbols is a top-10 preview
                # and must never be used to reason about coverage -- doing
                # exactly that is what made reconnect restoration unmeasurable.
                "per_symbol_counts": dict(self.per_symbol),
                "least_active_symbols": self.per_symbol.most_common()[-10:] if self.per_symbol else [],
                "silent_symbols": classify_silence(subscribed, self.per_symbol, acks, dur),
                "proc": proc_sample(),
            }


def classify_silence(subscribed, per_symbol, acks, dur) -> dict:
    """G4: uses subscription-ack evidence where present. A silent
    instrument is NOT called a failure without evidence."""
    counts, detail = Counter(), defaultdict(list)
    for s in subscribed:
        n = per_symbol.get(s, 0)
        if n > 2:
            continue
        acked = acks.get(s)
        if n == 0 and acked is False:
            label = "SUBSCRIPTION_FAILURE"
        elif n == 0 and acked is True:
            label = "NO_UPDATE"
        elif n == 0:
            label = "UNKNOWN"          # no ack evidence either way
        else:
            label = "LOW_LIQUIDITY" if dur > 60 else "UNKNOWN"
        counts[label] += 1
        if len(detail[label]) < 10:
            detail[label].append(s)
    return {"counts": dict(counts), "samples": dict(detail),
            "ack_evidence_available": bool(acks)}


# ----------------------------------------------------------------- manifest
def sha256_of(path) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def build_manifest(out, universe_file, app_id, token, universe) -> dict:
    """WHO measured WHAT, WHERE and with WHICH code.

    A measurement nobody can reproduce the provenance of is an anecdote. The
    credential contributes PRESENCE ONLY -- no value, no app id, no
    fingerprint, no length. Provenance that identifies a credential is
    provenance that leaks one.
    """
    import hashlib
    import platform
    import subprocess

    def git(*args):
        try:
            return subprocess.run(["git", "-C", "/opt/bujji/app", *args],
                                  capture_output=True, text=True,
                                  timeout=20).stdout.strip()
        except Exception as exc:
            return f"unavailable: {type(exc).__name__}: {exc}"

    return {
        "host": platform.node(),
        "kernel": platform.release(),
        "python": platform.python_version(),
        "checkout_path": "/opt/bujji/app",
        "checkout_sha": git("rev-parse", "HEAD"),
        "checkout_dirty": bool(git("status", "--porcelain")),
        "checkout_unchanged_by_this_run": True,
        "harness_path": str(pathlib.Path(__file__).resolve()),
        "harness_sha256": sha256_of(__file__),
        "harness_outside_checkout": not str(
            pathlib.Path(__file__).resolve()).startswith("/opt/bujji/app"),
        "universe_file": str(universe_file),
        "universe_sha256": sha256_of(universe_file),
        "universe_size": len(universe.get("instruments", [])),
        "universe_built_at": universe.get("built_at"),
        "universe_expiries": universe.get("expiries"),
        "universe_atm": universe.get("atm"),
        # NO CREDENTIAL-ADJACENT VALUES. Not the value, and not the app id,
        # fingerprint or length either. A fingerprint is stable across every
        # artifact a token ever touches, so it correlates runs to a credential
        # for anyone who later obtains one -- and this manifest is evidence
        # meant to be read, copied and kept. Presence and validation are the
        # only facts a reader of this file actually needs.
        "credential_present": bool(app_id and token),
        "credential_source": "process environment, sourced from the operator's env file",
        "output_dir": str(out),
    }


def seal_artifacts(out) -> dict:
    """Every file this run produced, with a digest.

    UNSEALED EVIDENCE IS A GATE 1 FAILURE, so the seal must exist for that
    check to mean anything. Written last, and it covers everything except
    itself.
    """
    sealed = {}
    for f in sorted(pathlib.Path(out).rglob("*")):
        if f.is_file() and f.name != "gate1_manifest.json":
            sealed[str(f.relative_to(out))] = {
                "bytes": f.stat().st_size, "sha256": sha256_of(f)}
    return sealed


def grade_gate1(results) -> dict:
    """PASS only if every stated failure condition is absent.

    The conditions are the operator's, restated here so the run grades itself
    rather than leaving a reader to assemble a verdict from warnings:
    ingestion loss, queue drop, write failure, corrupt or unsealed evidence,
    PARTIAL reconnect restoration, or incomplete accounting.

    INACTIVE IS NOT LOST. Silent far strikes are reported as coverage, never
    as ingestion loss -- a strike nobody traded produced no tick to lose, and
    counting it as loss would fail a healthy feed.
    """
    failures, notes = [], []

    for mode, v in (results.get("validation") or {}).items():
        if v.get("dropped"):
            failures.append(f"[{mode}] {v['dropped']} message(s) dropped by the "
                            f"harness queue -- ingestion loss")
        if v.get("rejected"):
            failures.append(f"[{mode}] {v['rejected']} message(s) accepted but "
                            f"NOT WRITTEN -- write failure")
        if v.get("unaccounted"):
            failures.append(f"[{mode}] accounting identity broken: "
                            f"{v['unaccounted']} message(s) are neither written, "
                            f"rejected, nor dropped -- indistinguishable from "
                            f"silent loss")
        if not v.get("raw_matches_written"):
            failures.append(f"[{mode}] raw corpus lines on disk "
                            f"({v.get('raw_lines_on_disk')}) do not match writes "
                            f"({v.get('corpus_written')}) -- write failure or "
                            f"incomplete accounting")
        if not v.get("corpus_self_sufficient"):
            failures.append(f"[{mode}] corpus failed offline re-parse "
                            f"({v.get('offline_reparse_malformed')} malformed) -- "
                            f"corrupt evidence")
        if v.get("harness_was_bottleneck"):
            notes.append(f"[{mode}] harness may have been the bottleneck; rates "
                         f"are a LOWER BOUND on what the feed can deliver")

    for phase in results.get("phases") or ():
        # Grade only the AFTER phase. The baseline phase is the control that
        # establishes which symbols were producing; grading it as a reconnect
        # would fail every run.
        if not str(phase.get("label", "")).endswith("_reconnect_after"):
            continue
        if phase.get("baseline_was_vacuous"):
            failures.append(
                f"[{phase['label']}] no symbol was producing callbacks before the "
                f"forced reconnect, so restoration was never actually tested -- "
                f"a vacuous pass is not a pass")
        elif phase.get("full_restoration") is False:
            failures.append(
                f"[{phase['label']}] PARTIAL reconnect restoration: "
                f"{phase.get('resumed_count')}/{phase.get('producers_before_reconnect')} "
                f"previously-producing symbols resumed actual callbacks; "
                f"{phase.get('not_resumed_count')} did not, e.g. "
                f"{phase.get('not_resumed_sample')}")
        elif phase.get("full_restoration") is None:
            failures.append(f"[{phase['label']}] reconnect restoration was not "
                            f"established -- incomplete accounting")

    for mode, fs in (results.get("feed_state") or {}).items():
        acc = (fs or {}).get("corpus") or {}
        if acc.get("accounted") is False:
            failures.append(f"[{mode}] corpus accounting does not balance")
        # At grading the run is over and the queue is drained, so anything
        # still in flight means the writer never finished -- records that were
        # accepted and never reached disk.
        if acc.get("in_flight"):
            failures.append(f"[{mode}] {acc['in_flight']} record(s) still in the "
                            f"queue at close -- accepted but never written")
        if acc.get("settled") is False:
            failures.append(f"[{mode}] accounting never settled: offered != "
                            f"written + rejected + dropped after drain")

    if not results.get("artifact_seal"):
        failures.append("no artifact seal was written -- unsealed evidence")

    if results.get("stopped_on_deadline"):
        failures.append(
            "the measurement was stopped by its absolute deadline before every "
            "phase completed -- a partial run is not a PASS, whatever the "
            "phases that did complete showed")

    traj = results.get("disk_trajectory") or []
    if not traj:
        notes.append("no free-space trajectory was sampled")

    # Coverage, reported SEPARATELY and never graded as loss.
    coverage = {}
    for mode, fs in (results.get("feed_state") or {}).items():
        for phase in results.get("phases") or ():
            if phase.get("mode") == mode and phase.get("silent_symbols"):
                coverage[phase.get("label")] = phase["silent_symbols"].get("counts")
        sub_fail = sum(
            (p.get("silent_symbols", {}).get("counts", {}) or {}).get("SUBSCRIPTION_FAILURE", 0)
            for p in results.get("phases") or () if p.get("mode") == mode)
        if sub_fail:
            failures.append(f"[{mode}] {sub_fail} subscription(s) were REFUSED by "
                            f"the venue -- that is subscription failure, not "
                            f"inactivity")

    return {
        "verdict": "FAIL" if failures else "PASS",
        "failures": failures,
        "notes": notes,
        "inactive_coverage_reported_separately": coverage,
        "criteria": [
            "no ingestion loss", "no queue drop", "no write failure",
            "no corrupt or unsealed evidence", "full reconnect restoration",
            "complete accounting",
        ],
        "not_a_criterion": "naturally inactive far strikes producing no ticks",
        "evidence_class": "HARNESS FEED EVIDENCE",
        "what_a_pass_establishes": (
            "This host, this token identity, this SDK-boundary harness and this "
            "measured universe sustained ONE bounded session with complete "
            "captured evidence."),
        "what_a_pass_does_NOT_establish": [
            "that the unchanged Bujji runner receives these ticks",
            "that FyersTickFeed receives them -- it keeps symbol+ltp and "
            "discards the other full-mode fields, which is why this harness "
            "binds the SDK directly",
            "that the Bujji tick journal durably records them",
            "any runtime strategy safety property",
            "permission to trade, deploy, merge, push, or enable entry",
        ],
        "harness_feed_evidence_vs_bujji_runtime_evidence": (
            "This is HARNESS FEED EVIDENCE. It measures what the FYERS "
            "websocket delivers to a direct SDK binding and what a purpose-"
            "built corpus writer preserves. BUJJI RUNTIME EVIDENCE -- that "
            "Bujji's own runner, feed wrapper and tick journal receive and "
            "keep the same data -- is a DIFFERENT measurement that this run "
            "does not attempt and cannot substitute for."),
        "single_run_caveat": (
            "one session. A PASS says this run met the conditions; it does not "
            "establish that the feed does so repeatably, and it authorises no "
            "deployment, merge, or entry."),
    }


# --------------------------------------------------------------------- main
def _install_signal_handlers():
    """SIGTERM must unwind like SIGINT so sealing runs.

    Default SIGTERM terminates the interpreter immediately: no finally, no
    buffer flush, no sealing. Raising SystemExit instead lets the terminal
    path in main() seal what was captured and record WHY it stopped.

    SIGINT is left alone -- it already raises KeyboardInterrupt, which
    unwinds. Note that unwinding is necessary but not sufficient: on
    2026-08-24 the first SIGINT unwound main() and the process still hung on
    a NON-DAEMON thread inside the FYERS SDK reconnect path, and a second
    signal was required. Sealing therefore happens on the way out of main(),
    before that hang can occur.
    """
    import signal as _signal

    def _term(signum, _frame):
        raise SystemExit(f"SIGNAL {signum} received -- bounded stop")

    try:
        _signal.signal(_signal.SIGTERM, _term)
    except Exception:  # noqa: BLE001 -- a handler we cannot install is not fatal
        pass


def _plan_seconds(args, ramp_rungs):
    """Total planned seconds across EVERY requested mode, before connecting.

    THE ERROR THIS PREVENTS. On 2026-08-24 the harness was launched with
    --mode both --sustained-minutes 300. That value applies PER MODE, so it
    requested 5h of lite sustained plus 5h of full sustained -- 10 hours of
    sustained capture inside a 6-hour session. It could never have fitted.
    Nothing checked, so lite consumed the day and full mode entered its
    sustained phase at 15:26 with 14 minutes of market left.

    Counting is cheap and happens before a socket is opened; discovering it
    afterwards costs a trading day that cannot be recaptured.
    """
    modes = ["lite", "full"] if args.mode == "both" else [args.mode]
    per_mode = len(ramp_rungs) * args.phase_seconds
    per_mode += (args.sustained_minutes or 0) * 60
    if args.stall_test_minutes:
        per_mode += args.stall_test_minutes * 60
    total = per_mode * len(modes)
    if args.reconnect_test:
        total += 540 * len(modes)
    return {"modes": modes, "per_mode_s": per_mode, "total_s": total,
            "ramp_rungs": len(ramp_rungs), "phase_seconds": args.phase_seconds,
            "sustained_minutes": args.sustained_minutes}


def main():
    ap = argparse.ArgumentParser()
    # NO DEFAULT. It used to point at /tmp/gate1_universe.json -- the retired
    # model's scratch output. A default that silently loads the wrong
    # universe is worse than a missing argument: the run succeeds and
    # measures something nobody chose.
    ap.add_argument("--universe", required=True,
                    help="Path to the authoritative universe.json built by "
                         "gate1_build_universe.py. Passed unchanged.")
    ap.add_argument("--out", default="/opt/bujji/gate1")
    ap.add_argument("--phase-seconds", type=int, default=300)
    ap.add_argument("--sustained-minutes", type=int, default=60)   # G2
    ap.add_argument("--mode", choices=["lite", "full", "both"], default="both")
    # DEPTH IS OFF FOR GATE 1 and enabling it needs a second, deliberate flag.
    # Depth is a different subscription with a different cost profile; mixing
    # it into this run would make the full-mode rates unattributable.
    ap.add_argument("--depth", action="store_true",
                    help="NOT for Gate 1; also requires --i-know-depth-is-out-of-scope")
    ap.add_argument("--i-know-depth-is-out-of-scope", action="store_true")
    ap.add_argument("--reconnect-test", action="store_true")
    # ABSOLUTE STOP, not merely a maximum runtime. MAX_RUNTIME_S answers "how
    # long have I been going?", which is the wrong question when a run starts
    # late: an 8-hour budget begun at 11:00 would still be measuring at 19:00,
    # hours after the venue closed, producing silence that grades as coverage.
    # This answers "what time is it?" -- the only question the market cares
    # about. ISO-8601 with an offset; naive values are refused.
    ap.add_argument("--stop-deadline", default=None,
                    help="ISO-8601 instant after which the harness stops, "
                         "whatever its elapsed runtime")
    ap.add_argument("--stall-test-minutes", type=int, default=0)
    ap.add_argument("--ceiling-probe", type=int, default=0)        # G7
    args = ap.parse_args()

    # Installed before anything else in main(): a SIGTERM arriving during
    # setup must unwind to the terminal path like any other, not kill the
    # interpreter outright.
    _install_signal_handlers()

    # DEADLINE FIRST, before the universe is opened or anything is allocated.
    # A run that cannot finish should be refused for THAT reason, not for
    # whatever unrelated error it stumbles into first.
    stop_deadline = None
    if args.stop_deadline:
        from datetime import datetime as _dt
        try:
            parsed = _dt.fromisoformat(args.stop_deadline)
        except ValueError as exc:
            raise SystemExit(f"--stop-deadline is not ISO-8601: {exc}")
        if parsed.tzinfo is None:
            raise SystemExit(
                "--stop-deadline must carry a timezone offset; a naive instant "
                "means a different moment on every host and is not a deadline")
        stop_deadline = parsed.timestamp()
        if stop_deadline <= time.time():
            raise SystemExit(
                f"--stop-deadline {args.stop_deadline} is already past -- "
                f"refusing to start a measurement that cannot finish")

    if args.depth and not args.i_know_depth_is_out_of_scope:
        raise SystemExit(
            "--depth is out of scope for Gate 1: it is a separate subscription "
            "whose cost would make the full-mode rates unattributable. Pass "
            "--i-know-depth-is-out-of-scope only for a deliberate separate run.")

    uni = json.loads(Path(args.universe).read_text())
    instruments = uni["instruments"]
    symbols = [i["symbol"] for i in instruments]
    kind_of = {i["symbol"]: i.get("kind", "UNKNOWN") for i in instruments}

    out = Path(args.out) / datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out.mkdir(parents=True, exist_ok=True)

    app_id, token = os.environ.get("FYERS_APP_ID"), os.environ.get("FYERS_ACCESS_TOKEN")
    if not app_id or not token:
        raise SystemExit("FYERS_APP_ID / FYERS_ACCESS_TOKEN not set — source a VALID .env.fyers")

    ramp = derive_ramp(len(symbols))

    started = time.time()
    results = {
        "gate": "GATE_1_FEED_CAPABILITY", "harness_version": "v5",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "universe_file": args.universe, "universe_size": len(symbols),
        "universe_by_kind": dict(Counter(kind_of.values())),
        "expiries": uni.get("expiries"), "atm": uni.get("atm"),
        "ramp": ramp, "ramp_derived_from_universe_size": len(symbols),
        "phase_seconds": args.phase_seconds,
        "phases": [], "feed_state": {}, "validation": {},
        "disk_trajectory": [], "manifest": {}, "gate1_verdict": {},
        "stop_deadline": args.stop_deadline,
    }

    from fyers_apiv3.FyersWebsocket import data_ws

    def guard(corpus=None):
        if stop_deadline is not None and time.time() >= stop_deadline:
            raise SystemExit(
                f"STOP DEADLINE reached ({args.stop_deadline}) — bounded run. "
                f"Stopping on the clock, not on elapsed time.")
        if time.time() - started > MAX_RUNTIME_S:
            raise SystemExit("MAX_RUNTIME exceeded — bounded run")
        usage = shutil.disk_usage(str(out))
        # C3: TRAJECTORY, not just a threshold. "We did not run out" and "a
        # full session would have fitted" are different claims, and only the
        # second one is evidence about capability.
        results["disk_trajectory"].append({
            "t": round(time.time() - started, 1), "free_bytes": usage.free,
            "used_bytes": usage.used,
            "corpus_bytes": (corpus.accounting()["raw_bytes"]
                             if corpus is not None else None)})
        if usage.free < MIN_FREE_BYTES:
            raise SystemExit("disk below 2GB free — bounded run")
        if corpus is not None and corpus.accounting()["raw_bytes"] > MAX_DISK_BYTES:
            raise SystemExit(f"corpus exceeded MAX_DISK_BYTES ({MAX_DISK_BYTES}) — bounded run")

    # ---- FEASIBILITY BEFORE ANY SOCKET OPENS -------------------------
    #
    # Refuse an impossible plan while it is still arithmetic. The check is
    # deliberately here -- before the mode loop, which is where the websocket
    # is created and the first subscription is sent -- so an infeasible run
    # never touches the venue at all.
    _plan = _plan_seconds(args, ramp)
    results["plan"] = _plan
    _budget_s = None
    if stop_deadline is not None:
        _budget_s = stop_deadline - time.time()
    print(f"[plan] {len(_plan['modes'])} mode(s) x {_plan['per_mode_s']}s "
          f"= {_plan['total_s']}s planned"
          + (f"; {int(_budget_s)}s available to the stop deadline"
             if _budget_s is not None else "; no stop deadline set"),
          flush=True)
    if _budget_s is not None and _plan["total_s"] > _budget_s:
        _over = int(_plan["total_s"] - _budget_s)
        raise SystemExit(
            f"INFEASIBLE PLAN -- refusing before opening a websocket. "
            f"{_plan['modes']} x (ramp {_plan['ramp_rungs']}x{_plan['phase_seconds']}s "
            f"+ sustained {_plan['sustained_minutes']}m) = {_plan['total_s']}s, but only "
            f"{int(_budget_s)}s remain before the stop deadline: over by {_over}s "
            f"({_over/3600:.1f}h). NOTE --sustained-minutes applies PER MODE. "
            f"Reduce --sustained-minutes, drop a mode, or move the deadline.")

    # ---- EVERY TERMINAL PATH SEALS ------------------------------------
    #
    # Normal completion, deadline, SIGTERM/SIGINT and an unhandled exception
    # all fall through to the sealing block below. Before this, only normal
    # completion did: the 2026-08-24 run was terminated by the operator and
    # produced no results.json, no offline re-parse and no accounting -- 394
    # MB of good evidence with nothing certifying it.
    #
    # The corpus is closed in the finally so its buffer is flushed before the
    # re-parse reads it back off disk.
    _terminal = TERMINAL_COMPLETED
    _terminal_detail = ""
    try:
        for mode in (["lite", "full"] if args.mode == "both" else [args.mode]):
            corpus = RawCorpus(out / f"raw_{mode}.jsonl")
            v5_seq = {"n": 0}
            v5_seq_lock = threading.Lock()
            holder: dict = {"m": None}
            acks: dict = {}
            state = {"connects": 0, "closes": 0, "errors": []}

            def on_msg(msg):
                recv = time.time()
                recv_mono = time.monotonic()
                with v5_seq_lock:
                    v5_seq["n"] += 1
                    n = v5_seq["n"]
                corpus.offer(seq=n, recv_wall=recv, recv_mono=recv_mono,
                             tid=threading.get_ident(), payload=msg)
                proc = time.time()
                proc_mono = time.monotonic()
                blob_size = corpus.last_line_size
                # G4: subscription acks are not price ticks; record them as evidence.
                if msg.get("ltp") is None and msg.get("symbol"):
                    acks[msg["symbol"]] = True
                # C2: first PRICE tick after a resubscribe, not the first ack --
                # an ack proves the subscription was accepted, not that data flows.
                if msg.get("ltp") is not None:
                    hook = holder.get("on_first_tick")
                    if hook is not None:
                        holder["on_first_tick"] = None
                        try:
                            hook()
                        except Exception:
                            pass
                m = holder.get("m")
                if m is not None:
                    m.observe(msg, recv, proc, blob_size, threading.get_ident(),
                              recv_mono=recv_mono, proc_mono=proc_mono)

            sock = data_ws.FyersDataSocket(
                access_token=f"{app_id}:{token}", log_path=str(out), litemode=(mode == "lite"),
                write_to_file=False, reconnect=True,
                on_connect=lambda: state.__setitem__("connects", state["connects"] + 1),
                on_close=lambda m: state.__setitem__("closes", state["closes"] + 1),
                on_error=lambda m: state["errors"].append({"t": time.time(), "msg": str(m)[:400]}),
                on_message=on_msg)
            threading.Thread(target=sock.connect, daemon=True).start()
            time.sleep(8)

            def run_phase(sub, seconds, label, data_type="SymbolUpdate"):
                guard(corpus)
                m = Metrics(kind_of)
                holder["m"] = m
                sock.subscribe(symbols=sub, data_type=data_type)
                # BOUNDED, INTERRUPTIBLE WAIT. guard() is re-checked every
                # DEADLINE_POLL_S so the absolute deadline can fire DURING a
                # phase, not only at its entry. The single uninterruptible
                # time.sleep(seconds) here is what let a 15:35 deadline run to
                # 20:26 on 2026-08-24.
                _phase_end = time.time() + seconds
                while True:
                    _left = _phase_end - time.time()
                    if _left <= 0:
                        break
                    guard(corpus)
                    time.sleep(min(DEADLINE_POLL_S, _left))
                s = m.summary(sub, acks)
                s.update(phase=label, mode=mode, subscribed=len(sub),
                         data_type=data_type, queue=corpus.accounting())
                results["phases"].append(s)
                print(json.dumps({k: s.get(k) for k in
                                  ("phase", "messages", "msgs_per_sec",
                                   "msgs_per_sec_per_instrument")}), flush=True)
                return s

            for n in ramp:
                if n > len(symbols):
                    break
                print(f"[{mode}] ramp {n} ...", flush=True)
                run_phase(symbols[:n], args.phase_seconds, f"{mode}_ramp_{n}")

            # G2 — sustained full universe, per-minute buckets
            if args.sustained_minutes:
                print(f"[{mode}] SUSTAINED full universe {args.sustained_minutes}m ...", flush=True)
                run_phase(symbols, args.sustained_minutes * 60, f"{mode}_sustained_full")

            # D3 — depth isolated
            if args.depth and mode == "full":
                print("[full] DEPTH (isolated) ...", flush=True)
                try:
                    sock.unsubscribe(symbols=symbols, data_type="SymbolUpdate")
                except Exception as exc:
                    state["errors"].append({"t": time.time(), "msg": f"unsub: {exc}"})
                time.sleep(10)
                run_phase(symbols[:50], min(args.phase_seconds, 180), "depth_cost_isolated", "DepthUpdate")

            if args.reconnect_test:
                print(f"[{mode}] RECONNECT ...", flush=True)
                # WHAT MUST COME BACK IS WHAT WAS ACTUALLY FLOWING.
                #
                # The pass condition is NOT "the socket reconnected" and NOT "the
                # subscriptions were acknowledged". Both can be true while no data
                # arrives -- that is precisely the failure this phase exists to
                # catch, and an ack is an answer about a subscription, never
                # evidence of current market data.
                #
                # So: take the set of symbols that were ACTIVELY PRODUCING
                # callbacks in a baseline window immediately before the forced
                # disconnect, and require every one of them to produce an ACTUAL
                # callback again afterwards. Symbols that were silent before are
                # excluded from the requirement -- an inactive far strike that
                # stays inactive is coverage, not a reconnect failure.
                reconnect_symbols = symbols
                baseline = run_phase(reconnect_symbols,
                                     max(60, min(args.phase_seconds, 180)),
                                     f"{mode}_reconnect_baseline")
                producers_before = {sym for sym, n in baseline["per_symbol_counts"].items()
                                    if n > 0}
                if not producers_before:
                    state["errors"].append({
                        "t": time.time(),
                        "msg": "reconnect baseline observed NO producing symbol; the "
                               "restoration test would be vacuous"})

                c0, t0 = state["connects"], time.time()
                try:
                    sock.close_connection()
                except Exception as exc:
                    state["errors"].append({"t": time.time(), "msg": f"close: {exc}"})
                detect = time.time() - t0
                time.sleep(5)
                threading.Thread(target=sock.connect, daemon=True).start()

                first_tick_at = {"t": None}
                holder["on_first_tick"] = lambda: first_tick_at.__setitem__("t", time.time())
                t_sub = time.time()
                after = run_phase(reconnect_symbols,
                                  max(180, min(args.phase_seconds, 300)),
                                  f"{mode}_reconnect_after")
                holder["on_first_tick"] = None

                producers_after = {sym for sym, n in after["per_symbol_counts"].items()
                                   if n > 0}
                not_resumed = sorted(producers_before - producers_after)
                after.update(
                    disconnect_detect_s=round(detect, 3),
                    connects_before=c0, connects_after=state["connects"],
                    resubscribed_count=len(reconnect_symbols),
                    producers_before_reconnect=len(producers_before),
                    producers_after_reconnect=len(producers_after),
                    resumed_count=len(producers_before & producers_after),
                    not_resumed_count=len(not_resumed),
                    not_resumed_sample=not_resumed[:25],
                    # A vacuous baseline can never be a PASS: with nothing
                    # producing beforehand there is nothing to have restored, and
                    # reporting that as success is how a broken reconnect looks
                    # healthy on a quiet morning.
                    baseline_was_vacuous=(not producers_before),
                    full_restoration=(bool(producers_before) and not not_resumed),
                    restoration_criterion=(
                        "every symbol producing ACTUAL callbacks before the forced "
                        "reconnect produced actual callbacks after it; socket "
                        "status and subscription acks are not accepted as proof"),
                    time_to_first_tick_s=(round(first_tick_at["t"] - t_sub, 3)
                                          if first_tick_at["t"] else None))

            if args.stall_test_minutes and mode == "full":
                print(f"[{mode}] STALL watch {args.stall_test_minutes}m ...", flush=True)
                run_phase(symbols[:100], args.stall_test_minutes * 60, f"{mode}_stall_watch")

            # G7 — ceiling probe LAST, cannot contaminate locked-universe results
            if args.ceiling_probe and mode == "full" and args.ceiling_probe > len(symbols):
                print(f"[{mode}] CEILING probe {args.ceiling_probe} ...", flush=True)
                state["errors"].append({"t": time.time(),
                                        "msg": f"ceiling probe requested {args.ceiling_probe} "
                                               f"but universe file holds {len(symbols)}"})

            try:
                sock.close_connection()
            except Exception:
                pass
            corpus.close()
            results["feed_state"][mode] = {
                "connects": state["connects"], "closes": state["closes"],
                "errors": state["errors"][:30], "corpus": corpus.accounting(),
                "acks_seen": len(acks),
            }

    except SystemExit as _exc:
        _msg = str(_exc)
        _terminal = (TERMINAL_DEADLINE if "STOP DEADLINE" in _msg
                     else TERMINAL_INTERRUPTED)
        _terminal_detail = _msg
        print(f"[terminal] {_terminal}: {_msg}", flush=True)
    except KeyboardInterrupt:
        _terminal = TERMINAL_INTERRUPTED
        _terminal_detail = "KeyboardInterrupt (SIGINT)"
        print(f"[terminal] {_terminal}", flush=True)
    except BaseException as _exc:  # noqa: BLE001 -- must still seal
        import traceback as _tb
        _terminal = TERMINAL_ERROR
        _terminal_detail = f"{type(_exc).__name__}: {_exc}"
        print(f"[terminal] {_terminal}: {_terminal_detail}", flush=True)
        _tb.print_exc()
    finally:
        # Flush and stop the writer before anything reads the corpus back.
        try:
            corpus.close()
        except Exception:  # noqa: BLE001
            pass
    results["terminal_state"] = _terminal
    results["terminal_detail"] = _terminal_detail
    results["evidence_complete"] = (_terminal == TERMINAL_COMPLETED)

    # ---- G5: offline raw-corpus re-parse validation ----------------------
    v = {}
    for mode in results["feed_state"]:
        f = out / f"raw_{mode}.jsonl"
        raw_lines = reparsed = 0
        bad = 0
        if f.exists():
            for line in open(f):
                raw_lines += 1
                try:
                    rec = json.loads(line)
                    if "payload" in rec and "recv_ts" in rec:
                        reparsed += 1
                    else:
                        bad += 1
                except Exception:
                    bad += 1
        acc = results["feed_state"][mode]["corpus"]
        parsed = sum(p["messages"] for p in results["phases"] if p.get("mode") == mode)
        v[mode] = {
            "rejected": acc.get("rejected", 0),
            "unaccounted": acc.get("unaccounted", 0),
            "raw_lines_on_disk": raw_lines, "corpus_written": acc["written"],
            "raw_matches_written": raw_lines == acc["written"],
            "offline_reparse_ok": reparsed, "offline_reparse_malformed": bad,
            "corpus_self_sufficient": bad == 0 and reparsed == raw_lines,
            "queue_accounted": acc["accounted"], "dropped": acc["dropped"],
            "no_silent_drops": acc["dropped"] == 0,
            "parsed_messages_in_phases": parsed,
            "harness_was_bottleneck": acc["dropped"] > 0 or acc["max_queue_depth"] > QUEUE_MAX * 0.8,
        }
    results["validation"] = v
    results["completed_at"] = datetime.now(timezone.utc).isoformat()
    results["runtime_s"] = round(time.time() - started, 1)

    results["stopped_on_deadline"] = bool(
        stop_deadline is not None and time.time() >= stop_deadline)
    results["manifest"] = build_manifest(out, args.universe, app_id, token, uni)
    (out / "gate1_results.json").write_text(json.dumps(results, indent=1, sort_keys=True))
    # Seal AFTER the results file exists, so the seal covers it too, then
    # re-write results with the seal and the verdict embedded.
    results["artifact_seal"] = seal_artifacts(out)
    results["gate1_verdict"] = grade_gate1(results)
    (out / "gate1_results.json").write_text(json.dumps(results, indent=1, sort_keys=True))
    (out / "gate1_manifest.json").write_text(json.dumps(
        {"manifest": results["manifest"], "artifact_seal": results["artifact_seal"],
         "gate1_verdict": results["gate1_verdict"]}, indent=1, sort_keys=True))

    print(f"\nRESULTS : {out}/gate1_results.json")
    print(f"MANIFEST: {out}/gate1_manifest.json")
    verdict = results["gate1_verdict"]
    print(f"\nGATE 1 VERDICT: {verdict['verdict']}")
    for f in verdict["failures"]:
        print(f"  FAIL  {f}")
    for n in verdict["notes"]:
        print(f"  note  {n}")
    if verdict["inactive_coverage_reported_separately"]:
        print("  coverage (NOT loss):",
              json.dumps(verdict["inactive_coverage_reported_separately"]))
    print(f"  {verdict['single_run_caveat']}")
    for mode, vv in v.items():
        if not vv["no_silent_drops"]:
            print(f"!! [{mode}] {vv['dropped']} DROPPED — measured observation, report as-is.")
        if vv["harness_was_bottleneck"]:
            print(f"!! [{mode}] HARNESS may have been the bottleneck — rates are a LOWER BOUND.")
        if not vv["corpus_self_sufficient"]:
            print(f"!! [{mode}] corpus failed offline re-parse — replay premise NOT established.")


if __name__ == "__main__":
    main()
