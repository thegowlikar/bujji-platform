"""Gate 1 — LIVE FEED CAPABILITY MEASUREMENT (v2, full spec).

READ-ONLY WITH RESPECT TO TRADING. Subscribes to market data and records.
No orders, no broker write path, no imports from execution / strategy /
risk / lifecycle / memory / PaperBroker.

Binds `data_ws.FyersDataSocket` DIRECTLY, not `FyersTickFeed`: the wrapper
keeps only `symbol`+`ltp` and discards the other 21 full-mode fields
(bujji/broker/fyers_ws.py:312-321). Gate 1 must measure the FEED's true
envelope, not the wrapper's filtering.

MEASURES REALITY. Makes no architectural decision: no TickStore, no
storage tier, no watermark, no aggregation policy, no universe reduction.
"""
from __future__ import annotations

import argparse
import json
import os
import queue
import shutil
import signal
import statistics
import sys
import threading
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

RAMP = [10, 50, 100, 200, 300, 411]
QUEUE_MAX = 500_000
MAX_DISK_BYTES = 20 * 1024**3      # hard stop: 20 GB
MAX_RUNTIME_S = 6 * 3600           # hard stop: 6 h


# --------------------------------------------------------------------------
# Raw corpus writer — evidence first, parsing second.
# --------------------------------------------------------------------------
class RawCorpus:
    """Append-only verbatim wire record. Written BEFORE any derivation.
    A full queue is real data loss: counted, never hidden."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._f = open(path, "a", buffering=4 * 1024 * 1024)
        self._q: queue.Queue = queue.Queue(maxsize=QUEUE_MAX)
        self._stop = threading.Event()
        self.offered = 0
        self.written = 0
        self.dropped = 0
        self.max_qdepth = 0
        self.write_latency_ms: list[float] = []
        self._t = threading.Thread(target=self._drain, daemon=True)
        self._t.start()

    def offer(self, rec: dict) -> None:
        self.offered += 1
        try:
            self._q.put_nowait(rec)
            d = self._q.qsize()
            if d > self.max_qdepth:
                self.max_qdepth = d
        except queue.Full:
            self.dropped += 1

    def _drain(self) -> None:
        while not self._stop.is_set() or not self._q.empty():
            try:
                rec = self._q.get(timeout=0.25)
            except queue.Empty:
                continue
            t0 = time.perf_counter()
            self._f.write(json.dumps(rec, separators=(",", ":")) + "\n")
            self.written += 1
            if self.written % 500 == 0:
                self.write_latency_ms.append((time.perf_counter() - t0) * 1000)

    @property
    def qdepth(self) -> int:
        return self._q.qsize()

    def close(self) -> None:
        self._stop.set()
        self._t.join(timeout=120)
        self._f.flush()
        os.fsync(self._f.fileno())
        self._f.close()

    def accounting(self) -> dict:
        return {
            "offered": self.offered, "written": self.written, "dropped": self.dropped,
            "drop_pct": round(100.0 * self.dropped / self.offered, 4) if self.offered else 0.0,
            "max_queue_depth": self.max_qdepth,
            "accounted": self.offered == self.written + self.dropped,
            "raw_bytes": self.path.stat().st_size if self.path.exists() else 0,
            "write_latency_ms_p50": round(statistics.median(self.write_latency_ms), 4)
                                     if self.write_latency_ms else None,
        }


def pctl(xs: list[float], p: float):
    if not xs:
        return None
    s = sorted(xs)
    i = min(int(len(s) * p), len(s) - 1)
    return round(s[i], 3)


def dist(xs: list[float]) -> dict:
    """Full distribution. NEGATIVE VALUES PRESERVED EXACTLY."""
    if not xs:
        return {"n": 0}
    return {
        "n": len(xs), "min": round(min(xs), 3), "max": round(max(xs), 3),
        "p50": pctl(xs, .50), "p90": pctl(xs, .90), "p95": pctl(xs, .95),
        "p99": pctl(xs, .99), "p999": pctl(xs, .999),
        "mean": round(statistics.fmean(xs), 3),
        "negative_count": sum(1 for x in xs if x < 0),
    }


# --------------------------------------------------------------------------
# Per-phase metrics
# --------------------------------------------------------------------------
class Metrics:
    def __init__(self, kind_of: dict):
        self.kind_of = kind_of
        self.field_present = Counter()
        self.field_null = Counter()
        self.per_symbol = Counter()
        self.per_kind = Counter()
        self.msg_types = Counter()
        self.exch_minus_recv_ms: list[float] = []   # raw offset (skew + latency)
        self.recv_to_proc_ms: list[float] = []
        self.sizes: list[int] = []
        self.first = None
        self.last = None
        self.last_recv: dict[str, float] = {}
        self.last_exch: dict[str, float] = {}
        self.gaps: dict[str, list[float]] = defaultdict(list)
        self.exch_nonmonotonic = 0
        self.recv_nonmonotonic = 0
        self.exact_duplicates = 0
        self._seen: set = set()
        self.per_second = Counter()
        self.total = 0

    def observe(self, p: dict, recv: float, proc: float, size: int) -> None:
        self.total += 1
        self.sizes.append(size)
        if self.first is None:
            self.first = recv
        self.last = recv
        self.per_second[int(recv)] += 1
        self.recv_to_proc_ms.append((proc - recv) * 1000.0)

        for k, v in p.items():
            (self.field_present if v is not None else self.field_null)[k] += 1

        sym = p.get("symbol", "<none>")
        self.per_symbol[sym] += 1
        self.per_kind[self.kind_of.get(sym, "UNKNOWN")] += 1
        self.msg_types[str(p.get("type"))] += 1

        sig = (sym, p.get("ltp"), p.get("exch_feed_time"), p.get("last_traded_time"))
        if sig in self._seen:
            self.exact_duplicates += 1
        else:
            self._seen.add(sig)

        prev_r = self.last_recv.get(sym)
        if prev_r is not None:
            self.gaps[sym].append(recv - prev_r)
            if recv < prev_r:
                self.recv_nonmonotonic += 1
        self.last_recv[sym] = recv

        e = p.get("exch_feed_time")
        if isinstance(e, (int, float)) and e > 1_000_000_000:
            prev_e = self.last_exch.get(sym)
            if prev_e is not None and e < prev_e:
                self.exch_nonmonotonic += 1
            self.last_exch[sym] = e
            self.exch_minus_recv_ms.append((recv - float(e)) * 1000.0)

    def summary(self, subscribed: list[str]) -> dict:
        dur = (self.last - self.first) if (self.first and self.last) else 0.0
        raw = self.exch_minus_recv_ms
        # Clock-skew estimate: network latency cannot be negative, so the
        # MINIMUM observed offset is the best available estimate of
        # (clock_offset + minimum_network_latency). Jitter above that floor
        # is the transport component. Reported separately, never merged.
        floor = min(raw) if raw else None
        jitter = [x - floor for x in raw] if floor is not None else []
        rates = list(self.per_second.values())
        silence = {s: (time.time() - self.last_recv[s]) for s in self.last_recv}
        return {
            "messages": self.total,
            "duration_s": round(dur, 2),
            "msgs_per_sec": round(self.total / dur, 3) if dur > 0 else None,
            "msgs_per_sec_per_instrument": round(self.total / dur / len(subscribed), 5)
                                            if dur > 0 and subscribed else None,
            "bytes_total": sum(self.sizes),
            "bytes_per_sec": round(sum(self.sizes) / dur, 1) if dur > 0 else None,
            "bytes_per_msg": dist([float(s) for s in self.sizes]),
            "burst_msgs_per_sec": {"max": max(rates) if rates else 0,
                                    "p95": pctl([float(r) for r in rates], .95),
                                    "median": statistics.median(rates) if rates else 0,
                                    "quiet_seconds": sum(1 for r in rates if r == 0)},
            "by_instrument_kind": dict(self.per_kind),
            "msg_types": dict(self.msg_types),
            "FIELD_ENVELOPE_present": dict(self.field_present.most_common()),
            "FIELD_ENVELOPE_null": dict(self.field_null.most_common()),
            "raw_exch_to_recv_ms": dist(raw),
            "clock_offset_floor_ms": round(floor, 3) if floor is not None else None,
            "transport_jitter_above_floor_ms": dist(jitter),
            "recv_to_processing_ms": dist(self.recv_to_proc_ms),
            "interarrival_s": dist([g for v in self.gaps.values() for g in v]),
            "exch_time_nonmonotonic": self.exch_nonmonotonic,
            "recv_time_nonmonotonic": self.recv_nonmonotonic,
            "exact_duplicate_msgs": self.exact_duplicates,
            "busiest_symbols": self.per_symbol.most_common(10),
            "least_active_symbols": self.per_symbol.most_common()[-10:] if self.per_symbol else [],
            "silent_symbols": classify_silence(subscribed, self.per_symbol, silence, dur),
        }


def classify_silence(subscribed, per_symbol, silence, dur) -> dict:
    """A silent instrument is NOT automatically a feed failure.
    Classify by evidence; default to UNKNOWN."""
    out = Counter()
    detail = defaultdict(list)
    for s in subscribed:
        n = per_symbol.get(s, 0)
        if n == 0:
            label = "NO_UPDATE"          # subscribed, zero messages this phase
        elif n <= 2 and dur > 60:
            label = "LOW_LIQUIDITY"
        else:
            continue
        out[label] += 1
        if len(detail[label]) < 10:
            detail[label].append(s)
    return {"counts": dict(out), "samples": {k: v for k, v in detail.items()},
            "note": "NO_UPDATE means zero msgs while subscribed. Distinguishing "
                    "SUBSCRIPTION_FAILURE from genuinely-untraded requires the "
                    "reconnect/ack evidence in feed_state."}


# --------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--universe", default="/tmp/gate1_universe.json")
    ap.add_argument("--out", default="/opt/bujji/gate1")
    ap.add_argument("--phase-seconds", type=int, default=300)
    ap.add_argument("--mode", choices=["lite", "full", "both"], default="both")
    ap.add_argument("--depth", action="store_true")
    ap.add_argument("--reconnect-test", action="store_true")
    ap.add_argument("--stall-test-minutes", type=int, default=0)
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
        "gate": "GATE_1_FEED_CAPABILITY",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "universe_file": args.universe,
        "universe_size": len(symbols),
        "universe_by_kind": dict(Counter(kind_of.values())),
        "expiries": uni.get("expiries"), "atm": uni.get("atm"),
        "ramp": RAMP, "phase_seconds": args.phase_seconds,
        "phases": [], "feed_state": {}, "validation": {},
    }

    from fyers_apiv3.FyersWebsocket import data_ws

    def guard():
        if time.time() - started > MAX_RUNTIME_S:
            raise SystemExit("MAX_RUNTIME exceeded — bounded run")
        free = shutil.disk_usage(str(out)).free
        if free < 2 * 1024**3:
            raise SystemExit(f"disk below 2GB free ({free}) — bounded run")

    for mode in (["lite", "full"] if args.mode == "both" else [args.mode]):
        corpus = RawCorpus(out / f"raw_{mode}.jsonl")
        holder: dict = {"m": None}
        state = {"connects": 0, "closes": 0, "errors": [], "connect_times": []}

        def on_msg(msg):
            recv = time.time()
            blob = json.dumps(msg, separators=(",", ":"))
            corpus.offer({"mode": mode, "recv_ts": recv, "size": len(blob), "payload": msg})
            proc = time.time()
            m = holder.get("m")
            if m is not None:
                m.observe(msg, recv, proc, len(blob))

        def on_connect():
            state["connects"] += 1
            state["connect_times"].append(time.time())

        def on_close(m):
            state["closes"] += 1

        def on_error(m):
            state["errors"].append({"t": time.time(), "msg": str(m)[:400]})

        sock = data_ws.FyersDataSocket(
            access_token=f"{app_id}:{token}", log_path=str(out), litemode=(mode == "lite"),
            write_to_file=False, reconnect=True,
            on_connect=on_connect, on_close=on_close, on_error=on_error, on_message=on_msg,
        )
        threading.Thread(target=sock.connect, daemon=True).start()
        time.sleep(8)

        for n in RAMP:
            if n > len(symbols):
                break
            guard()
            sub = symbols[:n]
            print(f"[{mode}] ramp {n} for {args.phase_seconds}s ...", flush=True)
            m = Metrics(kind_of)
            holder["m"] = m
            sock.subscribe(symbols=sub, data_type="SymbolUpdate")
            time.sleep(args.phase_seconds)
            s = m.summary(sub)
            s.update(phase=f"{mode}_ramp_{n}", mode=mode, ramp=n, subscribed=n,
                     data_type="SymbolUpdate", queue=corpus.accounting())
            results["phases"].append(s)
            print(json.dumps({k: s.get(k) for k in
                              ("messages", "msgs_per_sec", "msgs_per_sec_per_instrument")}), flush=True)

        if args.depth and mode == "full":
            guard()
            print("[full] DEPTH cost experiment ...", flush=True)
            m = Metrics(kind_of); holder["m"] = m
            sub = symbols[:50]
            sock.subscribe(symbols=sub, data_type="DepthUpdate")
            time.sleep(min(args.phase_seconds, 180))
            s = m.summary(sub)
            s.update(phase="depth_cost", mode=mode, data_type="DepthUpdate",
                     subscribed=len(sub), queue=corpus.accounting())
            results["phases"].append(s)

        if args.reconnect_test:
            guard()
            print(f"[{mode}] RECONNECT experiment ...", flush=True)
            c0, t0 = state["connects"], time.time()
            try:
                sock.close_connection()
            except Exception as exc:
                state["errors"].append({"t": time.time(), "msg": f"close: {exc}"})
            disconnect_detected = time.time() - t0
            time.sleep(5)
            threading.Thread(target=sock.connect, daemon=True).start()
            m = Metrics(kind_of); holder["m"] = m
            sub = symbols[:100]
            sock.subscribe(symbols=sub, data_type="SymbolUpdate")
            t_sub = time.time()
            time.sleep(180)
            s = m.summary(sub)
            s.update(phase=f"{mode}_reconnect", mode=mode, subscribed=len(sub),
                     disconnect_detect_s=round(disconnect_detected, 3),
                     connects_before=c0, connects_after=state["connects"],
                     time_to_first_tick_s=round(m.first - t_sub, 3) if m.first else None,
                     symbols_restored=len(m.per_symbol),
                     symbols_missing=len(set(sub) - set(m.per_symbol)),
                     queue=corpus.accounting())
            results["phases"].append(s)

        if args.stall_test_minutes and mode == "full":
            guard()
            print(f"[{mode}] SILENT-STALL watch {args.stall_test_minutes}m ...", flush=True)
            m = Metrics(kind_of); holder["m"] = m
            sub = symbols[:100]
            sock.subscribe(symbols=sub, data_type="SymbolUpdate")
            watch, tick = [], 30
            for _ in range(args.stall_test_minutes * 60 // tick):
                time.sleep(tick)
                watch.append({"t": round(time.time() - started, 1), "msgs": m.total,
                              "qdepth": corpus.qdepth, "connects": state["connects"]})
            s = m.summary(sub)
            s.update(phase=f"{mode}_stall_watch", mode=mode, subscribed=len(sub),
                     timeline=watch, queue=corpus.accounting())
            results["phases"].append(s)

        try:
            sock.close_connection()
        except Exception:
            pass
        corpus.close()
        results["feed_state"][mode] = {
            "connects": state["connects"], "closes": state["closes"],
            "errors": state["errors"][:30], "corpus": corpus.accounting(),
        }

    # ---- post-run validation --------------------------------------------
    v = {}
    for mode, fs in results["feed_state"].items():
        raw_lines = sum(1 for _ in open(out / f"raw_{mode}.jsonl")) if (out / f"raw_{mode}.jsonl").exists() else 0
        acc = fs["corpus"]
        parsed = sum(p["messages"] for p in results["phases"] if p.get("mode") == mode)
        v[mode] = {
            "raw_lines_on_disk": raw_lines,
            "corpus_written": acc["written"],
            "raw_matches_written": raw_lines == acc["written"],
            "queue_accounted": acc["accounted"],
            "dropped": acc["dropped"],
            "no_silent_drops": acc["dropped"] == 0,
            "parsed_messages": parsed,
            "parsed_le_written": parsed <= acc["written"] + acc["dropped"],
            "harness_was_bottleneck": acc["dropped"] > 0 or acc["max_queue_depth"] > QUEUE_MAX * 0.8,
        }
    results["validation"] = v
    results["completed_at"] = datetime.now(timezone.utc).isoformat()
    results["runtime_s"] = round(time.time() - started, 1)

    (out / "gate1_results.json").write_text(json.dumps(results, indent=1, sort_keys=True))
    print(f"\nRESULTS : {out}/gate1_results.json")
    print(f"CORPUS  : {out}/raw_*.jsonl")
    for mode, vv in v.items():
        if not vv["no_silent_drops"]:
            print(f"!! [{mode}] {vv['dropped']} DROPPED — observation, not a failure of the feed. "
                  "Report as measured.")
        if vv["harness_was_bottleneck"]:
            print(f"!! [{mode}] HARNESS may have been the bottleneck — interpret rates as a LOWER BOUND.")


if __name__ == "__main__":
    main()
