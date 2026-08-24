"""Read a sealed Gate 1 corpus back, verify it, and analyse it. Read-only.

WHAT THIS IS FOR. Until now the corpus was written and never read: the only
thing that touched it afterwards was a JSON-validity loop that counted
parseable lines. Every number Gate 1 reported was computed live, in memory,
and survived only as a summary. That makes the corpus an artifact nobody has
ever used, and an unread artifact is an unproven one.

VERIFY BEFORE ANALYSING, ALWAYS. A number derived from an unsealed, truncated,
or tampered corpus is worse than no number, because it looks the same as a
good one. Certification requires, in this order:

    artifact hash  -> the bytes are the bytes that were sealed
    manifest schema-> the manifest describes this run
    accounting     -> offered == written + rejected + dropped
    sequence       -> contiguous from 1, no holes, no duplicates

Any failure downgrades the run to ANALYSIS-ONLY: figures are still produced,
because they are useful for diagnosis, but the run cannot be certified.

SEQUENCE IS ARRIVAL ORDER; FILE ORDER IS NOT. Records are sorted by `seq`
before analysis. Two callback threads can reach the queue out of order, so
trusting line order would silently mis-measure every interarrival gap.

MONOTONIC TIME FOR DURATIONS, WALL CLOCK FOR CORRELATION. A wall-clock step
in the middle of a session would otherwise appear as a gap that never
happened.

WHAT IS CAPTURED, STATED PRECISELY. This reads the FULL SDK CALLBACK PAYLOAD
as the fyers_apiv3 library delivered it. It is NOT the raw exchange wire
frame: the SDK decodes the binary feed and hands over a Python dict, and
whatever it dropped or renamed before that point is not recoverable here and
is not claimed to be.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

# Fields a full-mode option/index callback may carry. Presence is MEASURED per
# field; nothing here asserts the SDK sends any of them.
MARKET_FIELDS = [
    "ltp", "bid_price", "ask_price", "bid_size", "ask_size", "bid_qty",
    "ask_qty", "open_interest", "oi", "prev_oi", "vol_traded_today", "volume",
    "last_traded_qty", "last_traded_time", "exch_feed_time", "high_price",
    "low_price", "open_price", "prev_close_price", "avg_trade_price",
    "tot_buy_qty", "tot_sell_qty", "type", "symbol", "ch", "chp",
]
DEPTH_HINTS = ["bids", "asks", "depth", "market_depth", "bid_price1", "ask_price1"]


def dist(xs):
    if not xs:
        return None
    xs = sorted(xs)
    def pct(p):
        if not xs:
            return None
        k = (len(xs) - 1) * p
        f, c = math.floor(k), math.ceil(k)
        return xs[int(k)] if f == c else xs[f] * (c - k) + xs[c] * (k - f)
    return {"n": len(xs), "min": xs[0], "p50": pct(.5), "p95": pct(.95),
            "p99": pct(.99), "max": xs[-1],
            "mean": round(statistics.fmean(xs), 6)}


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--session-dir", required=True)
    ap.add_argument("--corpus", default="raw_full.jsonl")
    ap.add_argument("--out", default=None)
    ap.add_argument("--silence-threshold-s", type=float, default=60.0)
    args = ap.parse_args()

    sess = Path(args.session_dir)
    corpus = sess / args.corpus
    report = {"schema": "gate1.replay/1", "session_dir": str(sess),
              "corpus": str(corpus), "verification": {}, "analysis": {},
              "certifiable": False}
    blockers = []

    if not corpus.exists():
        print(f"no corpus at {corpus}", file=sys.stderr)
        return 2

    # ---- 1. artifact hash against the seal ------------------------------
    seal_path = sess / "gate1_manifest.json"
    actual = sha256_of(corpus)
    sealed = None
    if seal_path.exists():
        try:
            seal_doc = json.loads(seal_path.read_text())
            sealed = ((seal_doc.get("artifact_seal") or {})
                      .get(args.corpus, {}) or {}).get("sha256")
        except Exception as exc:
            blockers.append(f"manifest unreadable: {type(exc).__name__}")
    else:
        blockers.append("no sealed manifest present -- corpus is UNSEALED")
    hash_ok = bool(sealed) and sealed == actual
    if sealed and not hash_ok:
        blockers.append("corpus hash does NOT match the seal -- tampered or truncated")
    report["verification"]["artifact_hash"] = {
        "sealed": sealed, "actual": actual, "match": hash_ok}

    # ---- 2. manifest schema ---------------------------------------------
    manifest_ok = False
    if seal_path.exists():
        try:
            man = (json.loads(seal_path.read_text()).get("manifest") or {})
            required = ["host", "checkout_sha", "harness_sha256", "universe_sha256"]
            missing = [k for k in required if not man.get(k)]
            manifest_ok = not missing
            if missing:
                blockers.append(f"manifest missing {missing}")
            report["verification"]["manifest"] = {
                "ok": manifest_ok, "missing": missing,
                "credential_free": not any(
                    k in man for k in ("fyers_app_id", "fyers_token_fingerprint",
                                       "fyers_token_length"))}
        except Exception as exc:
            blockers.append(f"manifest schema unreadable: {type(exc).__name__}")

    # ---- 3. read, sort by sequence --------------------------------------
    records, malformed, no_seq = [], 0, 0
    for line in open(corpus):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            malformed += 1
            continue
        if "seq" not in r:
            no_seq += 1
            continue
        records.append(r)
    if malformed:
        blockers.append(f"{malformed} malformed line(s) -- corpus is CORRUPT")
    if no_seq:
        blockers.append(f"{no_seq} record(s) carry no sequence -- arrival order "
                        f"cannot be established for them")

    records.sort(key=lambda r: r["seq"])
    seqs = [r["seq"] for r in records]
    file_order_differs = seqs != [r["seq"] for r in
                                  sorted(records, key=lambda r: 0)] if False else None
    dupes = [s for s, c in Counter(seqs).items() if c > 1]
    holes = []
    if seqs:
        expected = set(range(seqs[0], seqs[-1] + 1))
        holes = sorted(expected - set(seqs))
    contiguous = bool(seqs) and not dupes and not holes and seqs[0] == 1
    if dupes:
        blockers.append(f"{len(dupes)} duplicate sequence number(s)")
    if holes:
        blockers.append(f"{len(holes)} sequence hole(s) -- records are missing "
                        f"from the corpus")
    if seqs and seqs[0] != 1:
        blockers.append(f"sequence starts at {seqs[0]}, not 1 -- the corpus is "
                        f"not the whole run")
    report["verification"]["sequence"] = {
        "records": len(records), "first": seqs[0] if seqs else None,
        "last": seqs[-1] if seqs else None, "contiguous": contiguous,
        "duplicates": len(dupes), "holes": len(holes),
        "holes_sample": holes[:20], "malformed_lines": malformed,
        "records_without_sequence": no_seq}

    # ---- 4. accounting identity from the results doc ---------------------
    res_path = sess / "gate1_results.json"
    if res_path.exists():
        try:
            res = json.loads(res_path.read_text())
            acc = (((res.get("feed_state") or {}).get("full") or {})
                   .get("corpus") or {})
            offered = acc.get("offered")
            if offered is not None:
                lhs = offered
                rhs = (acc.get("written", 0) + acc.get("rejected", 0)
                       + acc.get("dropped", 0))
                ident_ok = lhs == rhs
                if not ident_ok:
                    blockers.append(
                        f"accounting identity broken: offered={lhs} != "
                        f"written+rejected+dropped={rhs}")
                report["verification"]["accounting"] = {
                    "offered": lhs, "written": acc.get("written"),
                    "rejected": acc.get("rejected"), "dropped": acc.get("dropped"),
                    "identity_holds": ident_ok,
                    "written_matches_corpus_lines":
                        acc.get("written") == len(records) + malformed + no_seq}
                if acc.get("dropped"):
                    blockers.append(f"{acc['dropped']} record(s) dropped at the queue")
                if acc.get("rejected"):
                    blockers.append(f"{acc['rejected']} record(s) rejected "
                                    f"(snapshot or write failure)")
        except Exception as exc:
            blockers.append(f"results doc unreadable: {type(exc).__name__}")
    else:
        blockers.append("no gate1_results.json -- accounting cannot be checked")

    # ---- 5. ANALYSIS, replayed in sequence order -------------------------
    per_symbol = Counter()
    field_present, field_null, field_absent = Counter(), Counter(), Counter()
    gaps = defaultdict(list)
    last_mono = {}
    wall_span = [None, None]
    mono_span = [None, None]
    msg_types = Counter()
    threads = Counter()
    exch_lateness = []
    max_exch = {}
    depth_seen = Counter()
    numeric_fields = defaultdict(list)

    for r in records:
        p = r.get("payload") or {}
        sym = p.get("symbol", "<none>")
        per_symbol[sym] += 1
        msg_types[str(p.get("type"))] += 1
        threads[r.get("tid")] += 1

        w, mo = r.get("recv_ts"), r.get("recv_monotonic")
        if isinstance(w, (int, float)):
            wall_span[0] = w if wall_span[0] is None else min(wall_span[0], w)
            wall_span[1] = w if wall_span[1] is None else max(wall_span[1], w)
        # DURATIONS FROM MONOTONIC ONLY.
        if isinstance(mo, (int, float)):
            mono_span[0] = mo if mono_span[0] is None else min(mono_span[0], mo)
            mono_span[1] = mo if mono_span[1] is None else max(mono_span[1], mo)
            prev = last_mono.get(sym)
            if prev is not None:
                gaps[sym].append(mo - prev)
            last_mono[sym] = mo

        for f in MARKET_FIELDS:
            if f in p:
                (field_present if p[f] is not None else field_null)[f] += 1
                if isinstance(p[f], (int, float)) and not isinstance(p[f], bool):
                    if len(numeric_fields[f]) < 200000:
                        numeric_fields[f].append(float(p[f]))
            else:
                field_absent[f] += 1
        for d in DEPTH_HINTS:
            if d in p and p[d] is not None:
                depth_seen[d] += 1

        e = p.get("exch_feed_time")
        if isinstance(e, (int, float)) and e > 1_000_000_000:
            prev_max = max_exch.get(sym)
            if prev_max is not None and e < prev_max:
                exch_lateness.append((prev_max - e) * 1000.0)
            else:
                max_exch[sym] = e

    duration = ((mono_span[1] - mono_span[0])
                if mono_span[0] is not None and mono_span[1] is not None else 0)
    all_gaps = [g for v in gaps.values() for g in v]

    # SILENCE, classified -- and never called loss.
    silent, low, active = [], [], []
    for sym, n in per_symbol.items():
        if n == 0:
            silent.append(sym)
        elif duration > args.silence_threshold_s and n <= 2:
            low.append(sym)
        else:
            active.append(sym)

    # UNAVAILABLE IS NOT ZERO. A field the SDK never sent is reported as
    # unavailable; reporting 0 would read as "no activity", which is a
    # different and false statement.
    availability = {}
    for f in MARKET_FIELDS:
        if field_present[f]:
            availability[f] = {"status": "PRESENT", "records_with_value":
                               field_present[f], "records_null": field_null[f],
                               "distribution": dist(numeric_fields.get(f, []))}
        elif field_null[f]:
            availability[f] = {"status": "PRESENT_BUT_ALWAYS_NULL",
                               "records_null": field_null[f]}
        else:
            availability[f] = {"status": "NOT_DELIVERED_BY_SDK",
                               "note": "absent from every callback; this is "
                                       "unavailability, NOT zero activity"}

    report["analysis"] = {
        "records_replayed": len(records),
        "replay_order": "sorted by callback sequence; file order not trusted",
        "duration_s_monotonic": round(duration, 3),
        "wall_clock_span_s": (round(wall_span[1] - wall_span[0], 3)
                              if None not in wall_span else None),
        "clock_skew_wall_minus_monotonic_s": (
            round((wall_span[1] - wall_span[0]) - duration, 3)
            if None not in wall_span and duration else None),
        "msgs_per_sec": round(len(records) / duration, 3) if duration else None,
        "per_symbol_counts": dict(per_symbol.most_common()),
        "per_symbol_rate": {s: round(n / duration, 5)
                            for s, n in per_symbol.most_common(50)} if duration else {},
        "interarrival_gaps_s": dist(all_gaps),
        "gap_clock": "monotonic",
        "coverage": {"active": len(active), "low_liquidity": len(low),
                     "never_produced": len(silent),
                     "note": "silence is COVERAGE, never ingestion loss"},
        "message_types": dict(msg_types),
        "callback_threads": {str(k): v for k, v in threads.items()},
        "exchange_timestamp_lateness_ms": dist(exch_lateness),
        "field_availability": availability,
        "depth_fields_seen": dict(depth_seen) or {
            "note": "no order-book depth field appeared in any callback"},
        "payload_provenance": (
            "FULL SDK CALLBACK PAYLOAD as fyers_apiv3 delivered it. NOT the raw "
            "exchange wire frame -- the SDK decodes the binary feed before this "
            "process sees anything, and whatever it dropped or renamed is not "
            "recoverable here and is not claimed."),
    }

    report["blockers"] = blockers
    report["certifiable"] = not blockers
    out = Path(args.out) if args.out else (sess / "gate1_replay_report.json")
    out.write_text(json.dumps(report, indent=1, sort_keys=True, default=str))

    print(f"REPLAY: {len(records)} record(s) from {corpus.name}")
    print(f"  sequence contiguous : {contiguous}")
    print(f"  artifact hash match : {hash_ok}")
    print(f"  duration (monotonic): {duration:.1f}s")
    print(f"  symbols             : {len(per_symbol)} "
          f"({len(active)} active, {len(low)} low-liquidity, {len(silent)} silent)")
    delivered = [f for f, a in availability.items() if a["status"] == "PRESENT"]
    missing = [f for f, a in availability.items()
               if a["status"] == "NOT_DELIVERED_BY_SDK"]
    print(f"  fields delivered    : {len(delivered)} -> {sorted(delivered)[:12]}")
    print(f"  fields NOT delivered: {len(missing)} -> {sorted(missing)[:12]}")
    if blockers:
        print(f"\n  NOT CERTIFIABLE ({len(blockers)}):")
        for b in blockers:
            print(f"    - {b}")
    else:
        print("\n  CERTIFIABLE: verified, then analysed.")
    print(f"  report: {out}")
    return 0 if not blockers else 1


if __name__ == "__main__":
    raise SystemExit(main())
