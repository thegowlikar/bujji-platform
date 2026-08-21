"""Gate 1 — FYERS feed capability measurement harness.

READ-ONLY MARKET DATA. Subscribes and records. Places no orders, touches
no broker write path, imports nothing from execution/strategy/lifecycle.

WHY IT DRIVES THE SDK DIRECTLY (not FyersTickFeed):
  `FyersTickFeed.on_message` keeps ONLY `symbol` and `ltp` and discards
  every other field (verified: bujji/broker/fyers_ws.py:312-321). Measuring
  through it would therefore measure the WRAPPER's filtering, not the
  FEED's true information envelope. Gate 1 must establish ground truth, so
  it binds `data_ws.FyersDataSocket` directly. The wrapper's reconnect
  behaviour is a separate question, tested separately.

Design constraints (from the re-baseline):
  * Raw payloads are IMMUTABLE AUTHORITATIVE EVIDENCE -> written verbatim,
    append-only, before any derivation.
  * The websocket callback MUST NOT block -> bounded queue + writer thread.
  * Dropped records are NEVER silent -> explicit counter, reported loudly.
  * Latency = receive_ts - exch_feed_time; ordering is never assumed.

Usage (source .env.fyers with a VALID token first):
  python gate1_measure.py --universe /tmp/gate1_universe.json \
      --out /opt/bujji/gate1 --phase-seconds 300 --mode both --reconnect-test
"""
from __future__ import annotations

import argparse
import json
import os
import queue
import statistics
import threading
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

RAMP = [10, 25, 50, 100, 200, 331]
QUEUE_MAX = 200_000


class Recorder:
    """Append-only raw evidence writer. One JSONL line per received
    message: verbatim payload + receive metadata. Never mutates."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._f = open(path, "a", buffering=1024 * 1024)
        self._q: queue.Queue = queue.Queue(maxsize=QUEUE_MAX)
        self._stop = threading.Event()
        self.dropped = 0
        self.written = 0
        self.max_qdepth = 0
        self._t = threading.Thread(target=self._drain, daemon=True)
        self._t.start()

    def offer(self, record: dict) -> None:
        """Called from the WS callback. Never blocks. A full queue is a
        REAL data-loss event: counted, never hidden."""
        try:
            self._q.put_nowait(record)
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
            self._f.write(json.dumps(rec, separators=(",", ":")) + "\n")
            self.written += 1

    def close(self) -> None:
        self._stop.set()
        self._t.join(timeout=60)
        self._f.flush()
        os.fsync(self._f.fileno())
        self._f.close()


class Metrics:
    def __init__(self):
        self.field_presence = Counter()
        self.field_null = Counter()
        self.msgs_per_symbol = Counter()
        self.msg_types = Counter()
        self.latencies_ms: list[float] = []
        self.first = None
        self.last = None
        self.last_seen: dict[str, float] = {}
        self.gaps: dict[str, list[float]] = defaultdict(list)
        self.total = 0
        self.bytes = 0

    def observe(self, p: dict, recv: float, raw_len: int) -> None:
        self.total += 1
        self.bytes += raw_len
        if self.first is None:
            self.first = recv
        self.last = recv
        for k, v in p.items():
            (self.field_presence if v is not None else self.field_null)[k] += 1
        sym = p.get("symbol", "<none>")
        self.msgs_per_symbol[sym] += 1
        self.msg_types[str(p.get("type"))] += 1
        prev = self.last_seen.get(sym)
        if prev is not None:
            self.gaps[sym].append(recv - prev)
        self.last_seen[sym] = recv
        e = p.get("exch_feed_time")
        if isinstance(e, (int, float)) and e > 1_000_000_000:
            self.latencies_ms.append((recv - float(e)) * 1000.0)

    def summary(self) -> dict:
        dur = (self.last - self.first) if (self.first and self.last) else 0.0
        lat = sorted(self.latencies_ms)
        allg = [g for v in self.gaps.values() for g in v]

        def pct(xs, p):
            return round(xs[int(len(xs) * p)], 2) if xs else None

        return {
            "messages": self.total,
            "duration_s": round(dur, 2),
            "msgs_per_sec": round(self.total / dur, 2) if dur > 0 else None,
            "distinct_symbols_seen": len(self.msgs_per_symbol),
            "bytes_raw": self.bytes,
            "bytes_per_msg": round(self.bytes / self.total, 1) if self.total else None,
            "msg_types": dict(self.msg_types),
            "FIELD_ENVELOPE_present": dict(self.field_presence.most_common()),
            "FIELD_ENVELOPE_null": dict(self.field_null.most_common()),
            "latency_ms": {"n": len(lat), "p50": pct(lat, .50), "p95": pct(lat, .95),
                            "p99": pct(lat, .99), "max": round(lat[-1], 2) if lat else None},
            "interarrival_s": {
                "n": len(allg),
                "p50": round(statistics.median(allg), 4) if allg else None,
                "p95": pct(sorted(allg), .95),
                "max": round(max(allg), 2) if allg else None,
            },
            "top_symbols": self.msgs_per_symbol.most_common(10),
        }


def build_socket(app_id, token, litemode, on_msg, out_dir, state):
    from fyers_apiv3.FyersWebsocket import data_ws

    def on_connect():
        state["connects"] += 1
        state["last_connect"] = time.time()

    def on_error(m):
        state["errors"].append({"t": time.time(), "msg": str(m)[:500]})

    def on_close(m):
        state["closes"] += 1

    return data_ws.FyersDataSocket(
        access_token=f"{app_id}:{token}",
        log_path=str(out_dir),
        litemode=litemode,
        write_to_file=False,
        reconnect=True,
        on_connect=on_connect,
        on_close=on_close,
        on_error=on_error,
        on_message=on_msg,
    )


def run_phase(sock, symbols, seconds, recorder, label, metrics_holder, data_type="SymbolUpdate"):
    m = Metrics()
    metrics_holder["current"] = m
    sock.subscribe(symbols=symbols, data_type=data_type)
    time.sleep(seconds)
    s = m.summary()
    seen = set(m.msgs_per_symbol)
    missing = set(symbols) - seen
    s.update(subscribed=len(symbols), silent_count=len(missing),
             silent_sample=sorted(missing)[:15], phase=label, data_type=data_type)
    return s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--universe", default="/tmp/gate1_universe.json")
    ap.add_argument("--out", default="/opt/bujji/gate1")
    ap.add_argument("--phase-seconds", type=int, default=300)
    ap.add_argument("--mode", choices=["lite", "full", "both"], default="both")
    ap.add_argument("--depth", action="store_true")
    ap.add_argument("--reconnect-test", action="store_true")
    args = ap.parse_args()

    uni = json.loads(Path(args.universe).read_text())
    symbols = [i["symbol"] for i in uni["instruments"]]
    out = Path(args.out) / datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out.mkdir(parents=True, exist_ok=True)

    app_id = os.environ.get("FYERS_APP_ID")
    token = os.environ.get("FYERS_ACCESS_TOKEN")
    if not app_id or not token:
        raise SystemExit("FYERS_APP_ID / FYERS_ACCESS_TOKEN not set -- source a VALID .env.fyers")

    results = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "universe_size": len(symbols),
        "universe_file": args.universe,
        "expiries": uni.get("expiries"),
        "atm": uni.get("atm"),
        "phases": [],
    }

    for mode in (["lite", "full"] if args.mode == "both" else [args.mode]):
        rec = Recorder(out / f"raw_{mode}.jsonl")
        holder: dict = {"current": None}
        state = {"connects": 0, "closes": 0, "errors": [], "last_connect": None}

        def on_msg(msg):
            recv = time.time()
            raw = json.dumps(msg, separators=(",", ":"))
            rec.offer({"mode": mode, "recv_ts": recv, "payload": msg})
            m = holder.get("current")
            if m is not None:
                m.observe(msg, recv, len(raw))

        sock = build_socket(app_id, token, mode == "lite", on_msg, out, state)
        threading.Thread(target=sock.connect, daemon=True).start()
        time.sleep(8)

        for n in RAMP:
            if n > len(symbols):
                break
            print(f"[{mode}] ramp {n} for {args.phase_seconds}s ...", flush=True)
            s = run_phase(sock, symbols[:n], args.phase_seconds, rec, f"{mode}_ramp_{n}", holder)
            s.update(mode=mode, ramp=n, queue_dropped=rec.dropped, max_queue_depth=rec.max_qdepth)
            results["phases"].append(s)
            print(json.dumps({k: s.get(k) for k in
                              ("messages", "msgs_per_sec", "distinct_symbols_seen",
                               "silent_count", "bytes_per_msg", "queue_dropped")}), flush=True)

        if args.depth and mode == "full":
            print("[full] depth capability test ...", flush=True)
            s = run_phase(sock, symbols[:25], 120, rec, "depth_test", holder, "DepthUpdate")
            results["phases"].append(s)

        if args.reconnect_test:
            print(f"[{mode}] reconnect test ...", flush=True)
            t0, c0 = time.time(), state["connects"]
            try:
                sock.close_connection()
            except Exception as exc:  # noqa: BLE001
                state["errors"].append({"t": time.time(), "msg": f"close: {exc}"})
            time.sleep(5)
            threading.Thread(target=sock.connect, daemon=True).start()
            s = run_phase(sock, symbols[:50], 180, rec, f"{mode}_after_reconnect", holder)
            s.update(mode=mode,
                     reconnect_recovery_s=round(time.time() - t0, 2),
                     connects_before=c0, connects_after=state["connects"],
                     resubscribe_worked=s["distinct_symbols_seen"] > 0)
            results["phases"].append(s)

        try:
            sock.close_connection()
        except Exception:  # noqa: BLE001
            pass
        rec.close()
        results.setdefault("feed_state", {})[mode] = {
            "connects": state["connects"], "closes": state["closes"],
            "errors": state["errors"][:20],
            "recorder_written": rec.written, "recorder_dropped": rec.dropped,
            "max_queue_depth": rec.max_qdepth,
            "raw_file_bytes": rec.path.stat().st_size,
        }

    (out / "gate1_results.json").write_text(json.dumps(results, indent=1))
    print(f"\nRESULTS: {out}/gate1_results.json")
    for mode, fs in results.get("feed_state", {}).items():
        if fs["recorder_dropped"]:
            print(f"!! [{mode}] {fs['recorder_dropped']} RECORDS DROPPED -- persistence "
                  "could not keep up. REAL finding; must be reported, never ignored.")


if __name__ == "__main__":
    main()
