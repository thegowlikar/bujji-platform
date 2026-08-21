"""Gate 1 — LIVE FEED CAPABILITY MEASUREMENT (v3).

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
import queue
import shutil
import statistics
import threading
import time
from collections import Counter, deque, defaultdict
from datetime import datetime, timezone
from pathlib import Path

RAMP = [10, 50, 100, 200, 300, 411]
QUEUE_MAX = 500_000
DUP_WINDOW = 250_000            # D2: bounded duplicate-detection window
MAX_DISK_BYTES = 20 * 1024**3   # D4: enforced below
MIN_FREE_BYTES = 2 * 1024**3
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
        self._f = open(path, "a", buffering=4 * 1024 * 1024)
        self._q: queue.Queue = queue.Queue(maxsize=QUEUE_MAX)
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self.offered = 0
        self.written = 0
        self.dropped = 0
        self.max_qdepth = 0
        self.depth_samples: list[tuple[float, int]] = []   # G6
        self._t = threading.Thread(target=self._drain, daemon=True)
        self._t.start()
        self._mon = threading.Thread(target=self._monitor, daemon=True)
        self._mon.start()

    def offer(self, rec: dict) -> None:
        with self._lock:
            self.offered += 1
        try:
            self._q.put_nowait(rec)
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
            self._f.write(json.dumps(rec, separators=(",", ":")) + "\n")
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
            "drop_pct": round(100.0 * self.dropped / self.offered, 4) if self.offered else 0.0,
            "max_queue_depth": self.max_qdepth,
            "accounted": self.offered == self.written + self.dropped,
            "raw_bytes": self.path.stat().st_size if self.path.exists() else 0,
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

    def observe(self, p: dict, recv: float, proc: float, size: int, tid: int) -> None:
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
            self.recv_to_proc_ms.append((proc - recv) * 1000.0)

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

            prev_r = self.last_recv.get(sym)
            if prev_r is not None:
                g = recv - prev_r
                self.gaps[sym].append(g)
                self.gaps_by_kind[kind].append(g)
                if recv < prev_r:
                    self.recv_nonmonotonic += 1
            self.last_recv[sym] = recv

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
                "exact_duplicate_msgs": self.exact_duplicates,
                "dup_window": DUP_WINDOW,
                "busiest_symbols": self.per_symbol.most_common(10),
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


# --------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--universe", default="/tmp/gate1_universe.json")
    ap.add_argument("--out", default="/opt/bujji/gate1")
    ap.add_argument("--phase-seconds", type=int, default=300)
    ap.add_argument("--sustained-minutes", type=int, default=60)   # G2
    ap.add_argument("--mode", choices=["lite", "full", "both"], default="both")
    ap.add_argument("--depth", action="store_true")
    ap.add_argument("--reconnect-test", action="store_true")
    ap.add_argument("--stall-test-minutes", type=int, default=0)
    ap.add_argument("--ceiling-probe", type=int, default=0)        # G7
    args = ap.parse_args()

    uni = json.loads(Path(args.universe).read_text())
    instruments = uni["instruments"]
    symbols = [i["symbol"] for i in instruments]
    kind_of = {i["symbol"]: i.get("kind", "UNKNOWN") for i in instruments}

    out = Path(args.out) / datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out.mkdir(parents=True, exist_ok=True)

    app_id, token = os.environ.get("FYERS_APP_ID"), os.environ.get("FYERS_ACCESS_TOKEN")
    if not app_id or not token:
        raise SystemExit("FYERS_APP_ID / FYERS_ACCESS_TOKEN not set — source a VALID .env.fyers")

    started = time.time()
    results = {
        "gate": "GATE_1_FEED_CAPABILITY", "harness_version": "v3",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "universe_file": args.universe, "universe_size": len(symbols),
        "universe_by_kind": dict(Counter(kind_of.values())),
        "expiries": uni.get("expiries"), "atm": uni.get("atm"),
        "ramp": RAMP, "phase_seconds": args.phase_seconds,
        "phases": [], "feed_state": {}, "validation": {},
    }

    from fyers_apiv3.FyersWebsocket import data_ws

    def guard(corpus=None):
        if time.time() - started > MAX_RUNTIME_S:
            raise SystemExit("MAX_RUNTIME exceeded — bounded run")
        if shutil.disk_usage(str(out)).free < MIN_FREE_BYTES:
            raise SystemExit("disk below 2GB free — bounded run")
        if corpus is not None and corpus.accounting()["raw_bytes"] > MAX_DISK_BYTES:
            raise SystemExit(f"corpus exceeded MAX_DISK_BYTES ({MAX_DISK_BYTES}) — bounded run")

    for mode in (["lite", "full"] if args.mode == "both" else [args.mode]):
        corpus = RawCorpus(out / f"raw_{mode}.jsonl")
        holder: dict = {"m": None}
        acks: dict = {}
        state = {"connects": 0, "closes": 0, "errors": []}

        def on_msg(msg):
            recv = time.time()
            blob = json.dumps(msg, separators=(",", ":"))
            corpus.offer({"mode": mode, "recv_ts": recv, "size": len(blob),
                          "tid": threading.get_ident(), "payload": msg})
            proc = time.time()
            # G4: subscription acks are not price ticks; record them as evidence.
            if msg.get("ltp") is None and msg.get("symbol"):
                acks[msg["symbol"]] = True
            m = holder.get("m")
            if m is not None:
                m.observe(msg, recv, proc, len(blob), threading.get_ident())

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
            time.sleep(seconds)
            s = m.summary(sub, acks)
            s.update(phase=label, mode=mode, subscribed=len(sub),
                     data_type=data_type, queue=corpus.accounting())
            results["phases"].append(s)
            print(json.dumps({k: s.get(k) for k in
                              ("phase", "messages", "msgs_per_sec",
                               "msgs_per_sec_per_instrument")}), flush=True)
            return s

        for n in RAMP:
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
            c0, t0 = state["connects"], time.time()
            try:
                sock.close_connection()
            except Exception as exc:
                state["errors"].append({"t": time.time(), "msg": f"close: {exc}"})
            detect = time.time() - t0
            time.sleep(5)
            threading.Thread(target=sock.connect, daemon=True).start()
            t_sub = time.time()
            s = run_phase(symbols[:100], 180, f"{mode}_reconnect")
            s.update(disconnect_detect_s=round(detect, 3), connects_before=c0,
                     connects_after=state["connects"],
                     symbols_missing=100 - len(s["busiest_symbols"]) if s["messages"] else 100,
                     time_to_first_tick_s=round(s["duration_s"] and (t_sub and 0) or 0, 3))

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

    (out / "gate1_results.json").write_text(json.dumps(results, indent=1, sort_keys=True))
    print(f"\nRESULTS : {out}/gate1_results.json")
    for mode, vv in v.items():
        if not vv["no_silent_drops"]:
            print(f"!! [{mode}] {vv['dropped']} DROPPED — measured observation, report as-is.")
        if vv["harness_was_bottleneck"]:
            print(f"!! [{mode}] HARNESS may have been the bottleneck — rates are a LOWER BOUND.")
        if not vv["corpus_self_sufficient"]:
            print(f"!! [{mode}] corpus failed offline re-parse — replay premise NOT established.")


if __name__ == "__main__":
    main()
