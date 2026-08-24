"""Gate 1 OPENING CAPTURE — subscribed before the bell, recording from it.

THE GAP THIS CLOSES. The previous design fetched a live spot after the open,
built the canonical universe from it, and only then subscribed. Everything
between 09:15:00 and the end of that build was uncaptured. A measurement with
a hole at the open is not a full-session measurement, whatever it says at the
bottom.

TWO STAGES, ONE BUILDER.

  PRE-OPEN   A PROVISIONAL universe, built through the SAME canonical
             `capture_universe.builder` from a prior-session reference price,
             with every tier widened by an opening buffer. Connected and
             subscribed before 09:15:00. Not authoritative, never used for
             trading -- it exists only so that nothing is missed at the bell.

  POST-OPEN  The CANONICAL universe, built from the first validated live spot
             by the same builder with normal tiers, handed over as a file.
             Every canonical symbol is then checked against what was actually
             subscribed before the open.

CONTAINMENT IS THE CLAIM, AND IT IS CONDITIONAL. If canonical is a subset of provisional, the
canonical universe was covered continuously FROM the open, and that is the
precise thing this run establishes. If any canonical symbol was NOT already
subscribed, it is subscribed immediately, its first-coverage time is recorded
to the millisecond, and the run is graded FAIL for opening coverage -- because
it had a gap, whatever the rest of the numbers show.

NOT CLAIMED: unconditional opening coverage. The provisional band is finite.
If the market opens beyond its buffer, canonical strikes fall outside what was
subscribed, and those ticks are missed -- detecting that afterwards does not
un-miss them. This run reports coverage as CONDITIONAL on containment holding,
and says so in the verdict rather than in a footnote.

Reuses RawCorpus / Metrics / sealing / grading from gate1_measure_v5 by
IMPORT, so there is one measurement model, not two.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import threading
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
RUN_DIR = Path(__file__).resolve().parent

_spec = importlib.util.spec_from_file_location(
    "g5", str(RUN_DIR / "gate1_measure_v5.py"))
G5 = importlib.util.module_from_spec(_spec)
sys.modules["g5"] = G5
_spec.loader.exec_module(G5)


def iso(dt) -> str:
    return dt.isoformat()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provisional-universe", required=True)
    ap.add_argument("--canonical-handoff", required=True,
                    help="path the orchestrator writes the canonical universe "
                         "to once a live spot is validated")
    ap.add_argument("--out", required=True)
    ap.add_argument("--open-at", required=True, help="ISO instant of the bell")
    ap.add_argument("--subscribe-by", required=True,
                    help="ISO instant by which all provisional subscriptions "
                         "must be submitted, or the run is abandoned")
    ap.add_argument("--stop-deadline", required=True)
    ap.add_argument("--handoff-timeout-s", type=int, default=1800)
    ap.add_argument("--first-spot-out", required=True,
                    help="written the instant the first valid post-open spot "
                         "callback arrives, so refinement starts then and not "
                         "on a timer")
    ap.add_argument("--spot-symbol", default="NSE:NIFTY50-INDEX")
    ap.add_argument("--reconnect-test", action="store_true")
    ap.add_argument("--reconnect-seconds", type=int, default=540)
    # CAPACITY PROBE -- last, always. It deliberately subscribes BEYOND the
    # measured universe, so anything running after it would be measuring a
    # different subscription set. Running it last means it cannot contaminate
    # a single number Gate 1 reports.
    ap.add_argument("--capacity-pool", default=None,
                    help="JSON universe to draw probe symbols from, typically "
                         "the role expiries in full")
    ap.add_argument("--capacity-probe-seconds", type=int, default=600)
    ap.add_argument("--capacity-steps", default="500,750,1000,1250,1465")
    ap.add_argument("--phase-seconds", type=int, default=300)
    args = ap.parse_args()

    open_at = datetime.fromisoformat(args.open_at)
    subscribe_by = datetime.fromisoformat(args.subscribe_by)
    stop_at = datetime.fromisoformat(args.stop_deadline)
    for name, val in (("--open-at", open_at), ("--subscribe-by", subscribe_by),
                      ("--stop-deadline", stop_at)):
        if val.tzinfo is None:
            raise SystemExit(f"{name} must carry a timezone offset")

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    prov = json.loads(Path(args.provisional_universe).read_text())
    prov_symbols = [i["symbol"] for i in prov["instruments"]]
    kind_of = {i["symbol"]: i.get("kind", "UNKNOWN") for i in prov["instruments"]}
    prov_set = set(prov_symbols)

    app_id, token = os.environ.get("FYERS_APP_ID"), os.environ.get("FYERS_ACCESS_TOKEN")
    if not app_id or not token:
        raise SystemExit("credentials absent from the environment")

    started = time.time()
    results = {
        "gate": "GATE_1_OPENING_CAPTURE", "harness_version": "opening/1",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "provisional_universe_file": args.provisional_universe,
        "provisional_symbol_count": len(prov_symbols),
        "provisional_buffer_points": prov.get("construction", {})
                                         .get("opening_buffer_points"),
        "open_at": args.open_at, "subscribe_by": args.subscribe_by,
        "stop_deadline": args.stop_deadline,
        "phases": [], "feed_state": {}, "validation": {},
        "disk_trajectory": [], "manifest": {}, "gate1_verdict": {},
        "opening_coverage": {},
    }

    from fyers_apiv3.FyersWebsocket import data_ws

    corpus = G5.RawCorpus(out / "raw_full.jsonl")
    holder: dict = {"m": None, "on_first_tick": None}
    acks: dict = {}
    state = {"connects": 0, "closes": 0, "errors": []}
    # INGEST ORDER AND RECEIVE TIME, assigned at the callback boundary before
    # anything else touches the message.
    seq = {"n": 0}
    seq_lock = threading.Lock()
    spot_symbol = args.spot_symbol
    first_spot_path = Path(args.first_spot_out)
    first_spot = {"written": False}
    open_ts: dict = {"t": None}
    first_seen: dict = {}
    first_seen_lock = threading.Lock()

    def guard(c=None):
        if time.time() >= stop_at.timestamp():
            raise SystemExit(f"STOP DEADLINE reached ({args.stop_deadline})")
        usage = __import__("shutil").disk_usage(str(out))
        results["disk_trajectory"].append(
            {"t": round(time.time() - started, 1), "free_bytes": usage.free,
             "corpus_bytes": c.accounting()["raw_bytes"] if c else None})
        if usage.free < G5.MIN_FREE_BYTES:
            raise SystemExit("disk below floor — bounded run")
        if c is not None and c.accounting()["raw_bytes"] > G5.MAX_DISK_BYTES:
            raise SystemExit("corpus exceeded cap — bounded run")

    def on_msg(msg):
        # DUAL CLOCK, both taken before anything else. Wall for correlating
        # with logs and exchange timestamps; monotonic for every duration,
        # because wall clock can step and a step is indistinguishable from
        # feed behaviour once it is in the distribution.
        recv = time.time()
        recv_mono = time.monotonic()
        with seq_lock:
            seq["n"] += 1
            n = seq["n"]
        # SNAPSHOT INSIDE offer(), on THIS thread, before the SDK can reuse the
        # object. The corpus never sees the mutable dict again.
        corpus.offer(seq=n, recv_wall=recv, recv_mono=recv_mono,
                     tid=threading.get_ident(), payload=msg)
        proc = time.time()
        proc_mono = time.monotonic()
        blob_size = corpus.last_line_size
        sym = msg.get("symbol")
        if msg.get("ltp") is None and sym:
            acks[sym] = True

        # FIRST VALID POST-OPEN SPOT, detected here rather than fetched on a
        # timer. Refinement used to wait a fixed 180s and then make a REST
        # call; that delay bought nothing -- coverage is already decided by
        # what was subscribed before the bell -- and it left the containment
        # question unanswered for three minutes longer than necessary.
        # "Valid" means: the spot instrument, a real positive price, and at or
        # after the bell. A pre-open quote is not an opening price.
        if (sym == spot_symbol and open_ts["t"] is not None
                and recv >= open_ts["t"] and not first_spot["written"]):
            ltp = msg.get("ltp")
            try:
                price = float(ltp)
            except (TypeError, ValueError):
                price = None
            if price and price > 0:
                first_spot["written"] = True
                try:
                    tmp = first_spot_path.with_suffix(".tmp")
                    tmp.write_text(json.dumps({
                        "spot": price,
                        "symbol": sym,
                        "observed_at_ist": iso(datetime.fromtimestamp(recv, IST)),
                        "seconds_after_open": round(recv - open_ts["t"], 3),
                        "source": "first valid post-open spot CALLBACK from the "
                                  "SDK boundary (not a REST poll, not a timer)",
                    }))
                    tmp.replace(first_spot_path)
                except Exception as exc:
                    state["errors"].append(
                        {"t": recv, "msg": f"first-spot write: {exc}"})
        if sym and msg.get("ltp") is not None:
            with first_seen_lock:
                if sym not in first_seen:
                    first_seen[sym] = recv
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
        access_token=f"{app_id}:{token}", log_path=str(out), litemode=False,
        write_to_file=False, reconnect=True,
        on_connect=lambda: state.__setitem__("connects", state["connects"] + 1),
        on_close=lambda m: state.__setitem__("closes", state["closes"] + 1),
        on_error=lambda m: state["errors"].append({"t": time.time(), "msg": str(m)[:400]}),
        on_message=on_msg)

    # ---- PRE-OPEN: connect and subscribe EVERYTHING ---------------------
    print(f"[pre-open] connecting; must be subscribed by "
          f"{subscribe_by:%H:%M:%S} IST", flush=True)
    threading.Thread(target=sock.connect, daemon=True).start()
    deadline = subscribe_by.timestamp()
    while state["connects"] == 0 and time.time() < deadline:
        time.sleep(0.5)
    if state["connects"] == 0:
        results["opening_coverage"] = {
            "status": "OPENING_COVERAGE_UNAVAILABLE",
            "reason": "the websocket did not connect before the subscription "
                      "deadline; a run started after the bell cannot be "
                      "presented as full-session evidence"}
        (out / "gate1_results.json").write_text(json.dumps(results, indent=1, sort_keys=True))
        print("OPENING_COVERAGE_UNAVAILABLE: no connection before the deadline",
              flush=True)
        return 20

    # SUBSCRIBE THE WHOLE PROVISIONAL SET AT ONCE. No subscription ramp here,
    # deliberately: a ramp that starts at a fraction of the universe would
    # leave most of it uncovered at the bell, which is the exact defect this
    # harness exists to remove. Capacity is measured from the sustained phase
    # and from per-minute buckets instead.
    sub_started = time.time()
    sock.subscribe(symbols=prov_symbols, data_type="SymbolUpdate")
    sub_done = time.time()
    results["subscription"] = {
        "submitted_count": len(prov_symbols),
        "submitted_at_ist": iso(datetime.fromtimestamp(sub_done, IST)),
        "submit_duration_s": round(sub_done - sub_started, 3),
        "before_deadline": sub_done < deadline,
        "note": "subscription acknowledgements establish intended coverage "
                "only; they are never evidence that market data is flowing",
    }
    if sub_done >= deadline:
        results["opening_coverage"] = {
            "status": "OPENING_COVERAGE_UNAVAILABLE",
            "reason": f"subscriptions completed at "
                      f"{datetime.fromtimestamp(sub_done, IST):%H:%M:%S}, after "
                      f"the {subscribe_by:%H:%M:%S} deadline"}
        (out / "gate1_results.json").write_text(json.dumps(results, indent=1, sort_keys=True))
        print("OPENING_COVERAGE_UNAVAILABLE: subscriptions completed too late",
              flush=True)
        return 20
    print(f"[pre-open] {len(prov_symbols)} subscriptions submitted at "
          f"{datetime.fromtimestamp(sub_done, IST):%H:%M:%S} IST", flush=True)

    # ---- THE BELL: recording is already live ----------------------------
    while datetime.now(IST) < open_at:
        time.sleep(0.2)
    opening_ts = time.time()
    open_ts["t"] = opening_ts          # arms the first-spot detector
    results["opening_capture_started_ist"] = iso(datetime.fromtimestamp(opening_ts, IST))
    results["messages_before_open"] = seq["n"]
    print(f"[OPEN] capture live at {datetime.fromtimestamp(opening_ts, IST):%H:%M:%S.%f} "
          f"IST; {seq['n']} message(s) already recorded pre-open", flush=True)

    # ---- POST-OPEN: canonical handoff and containment -------------------
    handoff = Path(args.canonical_handoff)
    m = G5.Metrics(kind_of)
    holder["m"] = m
    waited = 0
    while not handoff.exists() and waited < args.handoff_timeout_s:
        guard(corpus)
        time.sleep(5)
        waited += 5

    if first_spot_path.exists():
        try:
            fs = json.loads(first_spot_path.read_text())
            results["first_post_open_spot"] = fs
            print(f"[refine] first valid post-open spot callback at "
                  f"+{fs['seconds_after_open']}s", flush=True)
        except Exception:
            pass

    if not handoff.exists():
        results["opening_coverage"] = {
            "status": "CANONICAL_UNIVERSE_MISSING",
            "reason": "no canonical universe was handed over; containment "
                      "could not be established, so continuous coverage of the "
                      "canonical set is UNPROVEN"}
        print("canonical handoff never arrived", flush=True)
    else:
        canon = json.loads(handoff.read_text())
        canon_symbols = [i["symbol"] for i in canon["instruments"]]
        canon_set = set(canon_symbols)
        missing = sorted(canon_set - prov_set)
        contained = not missing
        cov = {
            "canonical_symbol_count": len(canon_set),
            "provisional_symbol_count": len(prov_set),
            "canonical_contained_in_provisional": contained,
            "missing_from_provisional_count": len(missing),
            "missing_from_provisional": missing[:50],
            "contextual_only_count": len(prov_set - canon_set),
            "handoff_at_ist": iso(datetime.now(IST)),
            "criterion": "every canonical symbol must have been subscribed "
                         "BEFORE the open; a symbol added afterwards was not "
                         "covered continuously, whatever it produced later",
        }
        if contained:
            cov["status"] = "CONTAINED"
            cov["proof"] = (
                f"all {len(canon_set)} canonical symbols were inside the "
                f"{len(prov_set)}-symbol provisional set subscribed at "
                f"{results['subscription']['submitted_at_ist']}, before the "
                f"bell -- so the canonical universe was covered continuously "
                f"from the open")
            print(f"[containment] HELD: {len(canon_set)} canonical symbols all "
                  f"pre-subscribed", flush=True)
        else:
            # SUBSCRIBE IMMEDIATELY, then grade FAIL. Closing the gap is the
            # right operational act; pretending it was not there is not.
            cov["status"] = "GAP"
            add_at = time.time()
            try:
                sock.subscribe(symbols=missing, data_type="SymbolUpdate")
                cov["late_subscription_submitted_ist"] = iso(
                    datetime.fromtimestamp(add_at, IST))
            except Exception as exc:
                cov["late_subscription_error"] = f"{type(exc).__name__}: {exc}"
            print(f"[containment] GAP: {len(missing)} canonical symbol(s) were "
                  f"NOT pre-subscribed -- subscribing now, run grades FAIL",
                  flush=True)
            time.sleep(60)
            with first_seen_lock:
                cov["first_coverage_ist"] = {
                    s: (iso(datetime.fromtimestamp(first_seen[s], IST))
                        if s in first_seen else None)
                    for s in missing[:50]}
                cov["gap_seconds_by_symbol"] = {
                    s: (round(first_seen[s] - opening_ts, 3)
                        if s in first_seen else None)
                    for s in missing[:50]}
        results["opening_coverage"] = cov

    # ---- sustained observation, ending early enough for the tail phases --
    reconnect_budget = args.reconnect_seconds if args.reconnect_test else 0
    probe_budget = args.capacity_probe_seconds if args.capacity_pool else 0
    sustained_until = stop_at.timestamp() - reconnect_budget - probe_budget
    print(f"[sustained] observing until "
          f"{datetime.fromtimestamp(sustained_until, IST):%H:%M:%S} IST "
          f"(reserving {reconnect_budget}s reconnect + {probe_budget}s probe)",
          flush=True)
    try:
        while time.time() < sustained_until:
            guard(corpus)
            time.sleep(30)
    except SystemExit as exc:
        print(f"[sustained] {exc}", flush=True)

    summary = m.summary(prov_symbols, acks)
    summary.update(phase="opening_sustained", mode="full",
                   subscribed=len(prov_symbols), queue=corpus.accounting())
    results["phases"].append(summary)

    # ---- RECONNECT: previously-producing symbols must resume -------------
    #
    # The pass condition is NOT "the socket came back" and NOT "the
    # subscriptions were acknowledged". Take the symbols ACTUALLY producing
    # callbacks before the forced disconnect and require every one of them to
    # produce again afterwards. Symbols silent beforehand are excluded -- an
    # inactive contract that stays inactive is coverage, not a failure.
    if args.reconnect_test and time.time() < stop_at.timestamp():
        print("[reconnect] forcing a disconnect", flush=True)
        producers_before = {sym for sym, n in
                            m.summary(prov_symbols, acks)["per_symbol_counts"].items()
                            if n > 0}
        c0, t0 = state["connects"], time.time()
        try:
            sock.close_connection()
        except Exception as exc:
            state["errors"].append({"t": time.time(), "msg": f"close: {exc}"})
        detect = time.time() - t0
        time.sleep(5)
        threading.Thread(target=sock.connect, daemon=True).start()
        t_sub = time.time()
        for _ in range(40):
            if state["connects"] > c0:
                break
            time.sleep(0.5)
        rm = G5.Metrics(kind_of)
        holder["m"] = rm
        first_after = {"t": None}
        holder["on_first_tick"] = lambda: first_after.__setitem__("t", time.time())
        try:
            sock.subscribe(symbols=prov_symbols, data_type="SymbolUpdate")
        except Exception as exc:
            state["errors"].append({"t": time.time(), "msg": f"resubscribe: {exc}"})
        window = max(120, min(args.reconnect_seconds - 60,
                              int(stop_at.timestamp() - time.time())))
        time.sleep(max(0, window))
        after = rm.summary(prov_symbols, acks)
        producers_after = {sym for sym, n in after["per_symbol_counts"].items() if n > 0}
        not_resumed = sorted(producers_before - producers_after)
        after.update(
            phase="opening_reconnect_after", mode="full",
            subscribed=len(prov_symbols), queue=corpus.accounting(),
            disconnect_detect_s=round(detect, 3),
            connects_before=c0, connects_after=state["connects"],
            producers_before_reconnect=len(producers_before),
            producers_after_reconnect=len(producers_after),
            resumed_count=len(producers_before & producers_after),
            not_resumed_count=len(not_resumed),
            not_resumed_sample=not_resumed[:25],
            baseline_was_vacuous=(not producers_before),
            full_restoration=(bool(producers_before) and not not_resumed),
            time_to_first_tick_s=(round(first_after["t"] - t_sub, 3)
                                  if first_after["t"] else None),
            restoration_criterion=(
                "every symbol producing ACTUAL callbacks before the forced "
                "reconnect produced actual callbacks after it; socket status "
                "and subscription acks are not accepted as proof"))
        results["phases"].append(after)
        holder["m"] = m
        print(f"[reconnect] {after['resumed_count']}/"
              f"{after['producers_before_reconnect']} producers resumed; "
              f"full_restoration={after['full_restoration']}", flush=True)

    # ---- CAPACITY PROBE: last, and reported separately -------------------
    #
    # Answers a question Gate 1 itself cannot: how many concurrent
    # subscriptions will the venue actually serve? That number decides whether
    # UNCONDITIONAL opening coverage is even purchasable -- covering the
    # canonical role expiries in full needs roughly 1,465, and nobody may
    # promise that until it is measured.
    #
    # NEVER AFFECTS THE GATE 1 VERDICT. It runs after every measured phase, it
    # subscribes beyond the measured universe on purpose, and its findings are
    # filed under `capacity_probe`, not under `validation`.
    if args.capacity_pool and time.time() < stop_at.timestamp():
        try:
            pool_doc = json.loads(Path(args.capacity_pool).read_text())
            pool = [i["symbol"] for i in pool_doc["instruments"]]
            steps = [int(x) for x in args.capacity_steps.split(",")
                     if int(x) <= len(pool)]
            probe = {"pool_size": len(pool), "steps": steps, "rungs": [],
                     "started_ist": iso(datetime.now(IST)),
                     "isolation": "ran AFTER every measured phase; subscribes "
                                  "beyond the measured universe by design and "
                                  "contaminates no Gate 1 figure",
                     "affects_gate1_verdict": False}
            dwell = max(20, (args.capacity_probe_seconds - 60) // max(1, len(steps)))
            for n in steps:
                if time.time() + dwell > stop_at.timestamp():
                    probe["stopped_early"] = "stop deadline"
                    break
                pm = G5.Metrics({sym: "OPTION" for sym in pool})
                holder["m"] = pm
                errs_before = len(state["errors"])
                ok = True
                try:
                    sock.subscribe(symbols=pool[:n], data_type="SymbolUpdate")
                except Exception as exc:
                    ok = False
                    state["errors"].append({"t": time.time(),
                                            "msg": f"probe subscribe({n}): {exc}"})
                time.sleep(dwell)
                ps = pm.summary(pool[:n], acks)
                producing = sum(1 for c in ps["per_symbol_counts"].values() if c > 0)
                rung = {"requested": n, "submit_ok": ok,
                        "symbols_producing": producing,
                        "coverage_pct": round(100.0 * producing / n, 1) if n else None,
                        "msgs_per_sec": ps.get("msgs_per_sec"),
                        "new_errors": len(state["errors"]) - errs_before,
                        "dwell_s": dwell}
                probe["rungs"].append(rung)
                print(f"[capacity] {json.dumps(rung)}", flush=True)
                # Submitted but producing nothing = a silent venue cap. Treat
                # it as the ceiling rather than climbing further and reporting
                # rates that describe a truncated subscription.
                if not ok or (producing == 0 and n > steps[0]):
                    probe["ceiling_at"] = n
                    print(f"[capacity] ceiling at {n}", flush=True)
                    break
            accepted = [r["requested"] for r in probe["rungs"]
                        if r["submit_ok"] and r["symbols_producing"]]
            probe["highest_accepted"] = max(accepted, default=0)
            probe["zero_gap_needs"] = (
                "roughly 1,465 concurrent subscriptions to cover the canonical "
                "role expiries in full, which is unconditional with respect to "
                "opening-move size because expiry roles are fixed before the "
                "bell (select_expiries takes as_of, not spot)")
            results["capacity_probe"] = probe
            holder["m"] = m
        except Exception as exc:
            results["capacity_probe"] = {"error": f"{type(exc).__name__}: {exc}",
                                         "affects_gate1_verdict": False}
            print(f"[capacity] probe failed: {exc}", flush=True)
    results["feed_state"]["full"] = {"corpus": corpus.accounting(), "socket": state}

    try:
        sock.close_connection()
    except Exception:
        pass
    corpus.close()

    # ---- validate, seal, grade ------------------------------------------
    raw_lines = reparsed = bad = 0
    for line in open(out / "raw_full.jsonl"):
        raw_lines += 1
        try:
            json.loads(line)
            reparsed += 1
        except Exception:
            bad += 1
    acc = corpus.accounting()
    results["validation"]["full"] = {
        "raw_lines_on_disk": raw_lines, "corpus_written": acc["written"],
        "raw_matches_written": raw_lines == acc["written"],
        "offline_reparse_ok": reparsed, "offline_reparse_malformed": bad,
        "corpus_self_sufficient": bad == 0 and reparsed == raw_lines,
        "dropped": acc["dropped"], "rejected": acc.get("rejected", 0),
        "unaccounted": acc.get("unaccounted", 0),
        "queue_accounted": acc["accounted"],
        "harness_was_bottleneck": acc["dropped"] > 0,
    }
    results["manifest"] = G5.build_manifest(out, args.provisional_universe,
                                            app_id, token, prov)
    (out / "gate1_results.json").write_text(json.dumps(results, indent=1, sort_keys=True))
    results["artifact_seal"] = G5.seal_artifacts(out)
    verdict = G5.grade_gate1(results)

    # OPENING COVERAGE IS ITS OWN FAILURE CONDITION.
    cov = results.get("opening_coverage") or {}
    if cov.get("status") == "GAP":
        verdict["failures"].append(
            f"OPENING COVERAGE GAP: {cov['missing_from_provisional_count']} "
            f"canonical symbol(s) were not subscribed before the bell; they "
            f"were added at {cov.get('late_subscription_submitted_ist')} and "
            f"were not covered continuously from the open")
    elif cov.get("status") != "CONTAINED":
        verdict["failures"].append(
            f"OPENING COVERAGE UNPROVEN: {cov.get('status')} -- "
            f"{cov.get('reason', 'containment was not established')}")
    verdict["verdict"] = "FAIL" if verdict["failures"] else "PASS"
    contained = cov.get("status") == "CONTAINED"
    verdict["tested_claim"] = (
        "CONDITIONAL opening coverage. This run establishes continuous capture "
        "from the open for the canonical universe ONLY IF canonical turned out "
        "to be a subset of the pre-open provisional set. That condition "
        "%s on this run." % ("HELD" if contained else "did NOT hold"))
    verdict["coverage_is_conditional"] = True
    verdict["coverage_condition"] = "canonical âŠ† provisional"
    verdict["what_this_does_not_establish"] = [
        "unconditional opening coverage -- the provisional band is finite, so "
        "an opening move beyond its buffer would place canonical strikes "
        "outside it",
        "that no canonical tick was missed in that case -- a gap detected "
        "after the fact is still a gap that already happened",
        "any claim about option contracts outside the captured universe",
    ]
    verdict["zero_gap_requires"] = (
        "either a provisional band wide enough to cover every strike that "
        "could enter canonical under an explicitly justified maximum opening "
        "move, or full subscription of the canonical role expiries. Expiry "
        "ROLES are fixed before the open (select_expiries takes as_of, not "
        "spot), so only the strike band moves -- which means covering the role "
        "expiries in full is unconditional with respect to opening-move size. "
        "Neither option may be promised until subscription capacity is "
        "measured.")
    results["gate1_verdict"] = verdict
    (out / "gate1_results.json").write_text(json.dumps(results, indent=1, sort_keys=True))
    (out / "gate1_manifest.json").write_text(json.dumps(
        {"manifest": results["manifest"], "artifact_seal": results["artifact_seal"],
         "gate1_verdict": verdict, "opening_coverage": cov}, indent=1, sort_keys=True))

    print(f"\nGATE 1 VERDICT: {verdict['verdict']}")
    for f in verdict["failures"]:
        print(f"  FAIL  {f}")
    return 0 if verdict["verdict"] == "PASS" else 10


if __name__ == "__main__":
    raise SystemExit(main())
