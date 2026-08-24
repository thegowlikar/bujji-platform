"""Compare Monday's measured Gate 1 evidence against the branch's assumptions.

READ-ONLY. Reads Gate 1 artifacts and the branch's provisional constants, and
prints a comparison. It changes no code, no policy, no configuration, and no
field map -- it produces the list of decisions those changes would be based on.

WHY A TOOL RATHER THAN A DOCUMENT. Every assumption below was written before
any payload was observed, and each one fails in a different direction. A key
we guessed wrong yields UNAVAILABLE for ever, silently, and looks identical to
a field the venue does not send. A freshness bound that is tighter than the
real interarrival makes healthy far strikes permanently stale. A capacity
belief that is too high makes zero-gap coverage a promise we cannot keep. None
of those are visible by reading a results file top to bottom; each needs the
measurement held against the assumption it was supposed to test.

Run after the session:
    python gate1_reconcile.py --session-dir /opt/bujji/gate1/GATE1_20260824
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BRANCH = "/opt/bujji/work-m4"
ZERO_GAP_SUBSCRIPTIONS = 1465          # role expiries in full, measured 2026-08-23
DEFAULT_MAX_PRICE_AGE_S = 90.0         # WebsocketTickProvider / _current_leg_quotes

OK, WARN, BAD, TODO = "OK", "REVIEW", "ACTION", "PENDING"
_rows = []


def row(section, item, status, measured, assumed, consequence=""):
    _rows.append({"section": section, "item": item, "status": status,
                  "measured": measured, "assumed": assumed,
                  "consequence": consequence})


def load(path: Path):
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


SCOPE_SELECTED = "selected/open legs + hedges"
SCOPE_ELIGIBLE = "pre-selection eligible band"
SCOPE_CONTEXTUAL = "contextual capture universe"


def _pct(xs, q):
    if not xs:
        return None
    xs = sorted(xs)
    k = (len(xs) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(xs) - 1)
    return round(xs[lo] + (xs[hi] - xs[lo]) * (k - lo), 3)


def scope_membership(session_dir: Path):
    """Split the captured symbols into the three decision scopes.

    WHY SCOPES AND NOT ONE NUMBER. An aggregate interarrival p95 mixes a
    far-month strike nobody trades with the leg an open short is priced from.
    The far strike is SUPPOSED to be quiet -- its silence is coverage. Judging
    the freshness standard against a distribution dominated by such symbols
    would loosen the bound for exactly the instruments that must never be
    priced from a stale quote.
    """
    prov = json.loads((session_dir / "provisional_universe.json").read_text()) \
        if (session_dir / "provisional_universe.json").exists() else None
    canon = json.loads((session_dir / "universe.json").read_text()) \
        if (session_dir / "universe.json").exists() else None
    if canon is None:
        return None

    canon_syms = {i["symbol"] for i in canon.get("instruments", [])}
    prov_syms = {i["symbol"] for i in (prov or canon).get("instruments", [])}
    atm = canon.get("atm_strike") or canon.get("atm")
    band = (canon.get("construction") or {}).get("selection_band_points")

    eligible = set()
    if atm and band:
        for i in canon.get("instruments", []):
            k = i.get("strike")
            if k is not None and abs(float(k) - float(atm)) <= float(band):
                eligible.add(i["symbol"])
    else:
        eligible = set(canon_syms)

    return {
        # A no-trade measurement session selects nothing and opens nothing.
        # Reported as NOT EXERCISED rather than as an empty pass.
        SCOPE_SELECTED: set(),
        SCOPE_ELIGIBLE: eligible,
        SCOPE_CONTEXTUAL: prov_syms - canon_syms,
        "_canonical": canon_syms,
    }


def per_symbol_gaps(corpus: Path, limit_bytes: int = 3_000_000_000):
    """Per-symbol interarrival, from MONOTONIC stamps, read from the corpus.

    Computed here rather than taken from the replay summary because the
    summary reports one aggregate distribution, and the whole point of this
    correction is that one distribution cannot answer a per-scope question.
    """
    if not corpus.exists():
        return None
    last, gaps, counts = {}, {}, {}
    read = 0
    with open(corpus) as fh:
        for line in fh:
            read += len(line)
            if read > limit_bytes:
                break
            try:
                r = json.loads(line)
            except Exception:
                continue
            sym = (r.get("payload") or {}).get("symbol")
            mono = r.get("recv_monotonic")
            if not sym or not isinstance(mono, (int, float)):
                continue
            if (r.get("payload") or {}).get("ltp") is None:
                continue                      # acks are coverage, not data
            counts[sym] = counts.get(sym, 0) + 1
            prev = last.get(sym)
            if prev is not None:
                gaps.setdefault(sym, []).append(mono - prev)
            last[sym] = mono
    return {"gaps": gaps, "counts": counts}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--session-dir", required=True)
    ap.add_argument("--branch", default=BRANCH)
    ap.add_argument("--json-out", default=None)
    args = ap.parse_args()

    sess = Path(args.session_dir)
    results = load(sess / "gate1_results.json")
    replay = load(sess / "gate1_replay_report.json")
    manifest = load(sess / "gate1_manifest.json")
    universe = load(sess / "universe.json")
    run_summary = load(sess / "gate1_run_summary.json")

    if results is None and replay is None:
        print(f"No Gate 1 artifacts under {sess}.", file=sys.stderr)
        print("Nothing to reconcile; the session has not produced evidence.",
              file=sys.stderr)
        return 2

    sys.path.insert(0, args.branch)
    from bujji.market_perception.quote import (
        PROVISIONAL_DEPTH_KEYS, PROVISIONAL_SDK_FIELD_MAP)

    # ---- 1. VERDICT AND INTEGRITY, before any figure is trusted ---------
    verdict = ((results or {}).get("gate1_verdict") or {})
    v = verdict.get("verdict")
    row("integrity", "Gate 1 verdict", OK if v == "PASS" else BAD, v,
        "PASS", "" if v == "PASS" else
        "figures below describe a run that failed its own conditions; read "
        "them as diagnosis, not as evidence")
    for f in (verdict.get("failures") or [])[:8]:
        row("integrity", "failure", BAD, str(f)[:110], "none")

    certifiable = (replay or {}).get("certifiable")
    row("integrity", "offline replay certifies the corpus",
        OK if certifiable else BAD, certifiable, True,
        "" if certifiable else "hash / manifest / accounting / sequence did "
        "not all verify -- no field claim below is admissible")
    for b in ((replay or {}).get("blockers") or [])[:6]:
        row("integrity", "replay blocker", BAD, str(b)[:110], "none")

    acc = (((results or {}).get("feed_state") or {}).get("full") or {}).get("corpus") or {}
    if acc:
        settled = acc.get("settled", acc.get("accounted"))
        row("integrity", "accounting identity settled",
            OK if settled else BAD, settled, True,
            "" if settled else "offered != written + rejected + dropped after "
            "drain -- unaccounted records are indistinguishable from loss")
        for k in ("dropped", "rejected", "in_flight"):
            n = acc.get(k, 0)
            row("integrity", k, OK if not n else BAD, n, 0)

    # ---- 2. THE FIELD MAP: the assumption most likely to be wrong -------
    avail = ((replay or {}).get("analysis") or {}).get("field_availability") or {}
    delivered = {k for k, a in avail.items()
                 if a.get("status") == "PRESENT"}
    null_only = {k for k, a in avail.items()
                 if a.get("status") == "PRESENT_BUT_ALWAYS_NULL"}
    absent = {k for k, a in avail.items()
              if a.get("status") == "NOT_DELIVERED_BY_SDK"}

    if not avail:
        row("field map", "replay field availability", TODO, "absent",
            "present", "the replay report carries no field envelope; the map "
            "cannot be reconciled")
    else:
        for logical, candidates in sorted(PROVISIONAL_SDK_FIELD_MAP.items()):
            hit = [c for c in candidates if c in delivered]
            nulls = [c for c in candidates if c in null_only]
            if hit:
                row("field map", logical, OK, f"delivered via {hit[0]}",
                    " | ".join(candidates))
            elif nulls:
                row("field map", logical, WARN, f"present but always null "
                    f"({nulls[0]})", " | ".join(candidates),
                    "the venue sends the key and never a value; treat as "
                    "unavailable rather than mapping it")
            else:
                seen_any = [c for c in candidates if c in avail]
                row("field map", logical, BAD,
                    "no mapped key delivered" + (f" (keys seen: {seen_any})"
                                                 if seen_any else ""),
                    " | ".join(candidates),
                    "this logical field is permanently UNAVAILABLE with the "
                    "current map -- correct the key or accept the field is "
                    "not obtainable")

        # Keys the venue sent that nothing maps: candidates we are discarding.
        mapped = {c for cs in PROVISIONAL_SDK_FIELD_MAP.values() for c in cs}
        unmapped = sorted(delivered - mapped)
        row("field map", "delivered but unmapped keys",
            WARN if unmapped else OK, unmapped or "none", "none",
            "these arrive on every callback and the projection ignores them; "
            "each is a candidate the runtime could use" if unmapped else "")

    depth = ((replay or {}).get("analysis") or {}).get("depth_fields_seen") or {}
    got_depth = [k for k in depth if k in PROVISIONAL_DEPTH_KEYS]
    row("field map", "order-book depth", OK if got_depth else WARN,
        got_depth or "not delivered", list(PROVISIONAL_DEPTH_KEYS),
        "" if got_depth else "no depth key appeared; depth-dependent logic is "
        "not implementable on this feed")

    # ---- 3. FRESHNESS, BY SCOPE. Never one aggregate number. ------------
    #
    # A change to the 90s bound is NOT proposed here and cannot be inferred
    # from any figure below. These are diagnostic inputs to an explicit
    # decision that also needs direct runtime evidence -- the harness measures
    # the feed, and the bound governs the runtime's own price path.
    scopes = scope_membership(sess)
    psg = per_symbol_gaps(sess / "raw_full.jsonl")

    if scopes is None or psg is None:
        agg = ((replay or {}).get("analysis") or {}).get("interarrival_gaps_s") or {}
        row("freshness", "per-scope breakdown", TODO,
            "corpus or universe artifacts absent",
            "per-symbol gaps split by scope",
            "falling back to the aggregate, which CANNOT justify a bound change")
        if agg:
            row("freshness", "aggregate interarrival p95 (diagnostic only)", WARN,
                f"{agg.get('p95')}s", f"current bound {DEFAULT_MAX_PRICE_AGE_S}s",
                "aggregate mixes quiet far strikes with tradable legs; not a "
                "basis for policy")
    else:
        for scope in (SCOPE_SELECTED, SCOPE_ELIGIBLE, SCOPE_CONTEXTUAL):
            members = scopes[scope]
            if scope == SCOPE_SELECTED and not members:
                row("freshness", f"{scope}", TODO, "NOT EXERCISED",
                    "a no-trade session selects nothing and opens nothing",
                    "the scope with the STRICTEST standard was never measured; "
                    "no freshness conclusion for open risk can come from this run")
                continue
            if not members:
                row("freshness", f"{scope}", TODO, "no members", "n/a")
                continue

            all_gaps = [g for sym in members for g in psg["gaps"].get(sym, [])]
            silent = sum(1 for sym in members if not psg["counts"].get(sym))
            p50, p95, p99 = _pct(all_gaps, .5), _pct(all_gaps, .95), _pct(all_gaps, .99)
            over = sum(1 for sym in members
                       if _pct(psg["gaps"].get(sym, []), .95) is not None
                       and _pct(psg["gaps"].get(sym), .95) > DEFAULT_MAX_PRICE_AGE_S)
            strict = scope in (SCOPE_SELECTED, SCOPE_ELIGIBLE)
            status = OK
            if strict and over:
                status = WARN
            row("freshness", f"{scope}: symbols", OK,
                f"{len(members)} ({silent} never produced)", "-")
            row("freshness", f"{scope}: gap p50/p95/p99", status,
                f"{p50}s / {p95}s / {p99}s",
                f"bound {DEFAULT_MAX_PRICE_AGE_S}s"
                + (" (STRICT scope)" if strict else " (context only)"),
                ("symbols in a STRICT scope show a p95 beyond the bound -- "
                 "record it, decide separately, and do NOT loosen the bound "
                 "on the strength of contextual symbols"
                 if strict and over else
                 "contextual sparseness is expected and must not relax the "
                 "standard applied to a trade candidate or open position"
                 if not strict else ""))
            row("freshness", f"{scope}: symbols with p95 over bound",
                status, over, 0 if strict else "-")

        row("freshness", "bound change proposed by this tool", OK, "none",
            "none -- explicit decision + runtime evidence required")

    # ---- 4. RATE AND THROUGHPUT ----------------------------------------
    a = (replay or {}).get("analysis") or {}
    row("throughput", "aggregate msgs/sec", OK, a.get("msgs_per_sec"),
        "unassumed -- Gate 1 establishes it")
    row("throughput", "records replayed", OK, a.get("records_replayed"),
        "unassumed")
    if acc:
        row("throughput", "queue high-water vs capacity",
            OK if (acc.get("max_queue_depth", 0) <
                   0.8 * (acc.get("queue_capacity") or 1)) else WARN,
            f"{acc.get('max_queue_depth')} / {acc.get('queue_capacity')}",
            "< 80% of capacity",
            "" if (acc.get("max_queue_depth", 0) <
                   0.8 * (acc.get("queue_capacity") or 1)) else
            "the harness approached its own bound; measured rates are a lower "
            "bound on what the feed can deliver")

    # ---- 5. CAPACITY = PRODUCERS, NOT ACKNOWLEDGEMENTS -------------------
    #
    # Capacity is the highest rung at which the venue actually DELIVERED
    # producer callbacks -- not the highest rung whose subscription request
    # was accepted. Those two numbers can differ by a lot, and the gap between
    # them is exactly the silent cap this probe exists to find: a venue that
    # accepts 1,500 subscriptions and serves 800 of them will look fine on
    # acknowledgements and lose a third of the book.
    #
    # An acknowledgement is coverage metadata. It records that a request was
    # accepted, and it is never proof of usable data.
    probe = (results or {}).get("capacity_probe") or {}
    rungs = probe.get("rungs") or []
    if not rungs:
        row("capacity", "capacity probe", TODO, "did not run",
            f"producer-confirmed rung >= {ZERO_GAP_SUBSCRIPTIONS}")
    else:
        accepted_rungs = [r["requested"] for r in rungs if r.get("submit_ok")]
        producing_rungs = [r["requested"] for r in rungs
                           if r.get("submit_ok") and r.get("symbols_producing")]
        highest_ack = max(accepted_rungs, default=0)
        highest_prod = max(producing_rungs, default=0)

        for r in rungs:
            n = r["requested"]
            prod = r.get("symbols_producing") or 0
            pct = r.get("coverage_pct")
            gap = r.get("submit_ok") and not prod
            row("capacity", f"rung {n}", BAD if gap else OK,
                f"request={'accepted' if r.get('submit_ok') else 'REFUSED'}, "
                f"producers={prod} ({pct}%)",
                "producers > 0 for the rung to count",
                "the request was accepted and NOTHING produced -- a silent "
                "venue cap, which is why acceptance is not capacity" if gap else "")

        row("capacity", "highest rung ACCEPTED (metadata only)", WARN,
            highest_ack, "not a capacity figure",
            "acknowledgement records that a request was accepted; it is never "
            "proof of usable data")
        enough = highest_prod >= ZERO_GAP_SUBSCRIPTIONS
        row("capacity", "CAPACITY: highest rung with producer callbacks",
            OK if enough else BAD, highest_prod,
            f">= {ZERO_GAP_SUBSCRIPTIONS} for unconditional coverage",
            "unconditional zero-gap coverage of the role expiries is "
            "purchasable" if enough else
            "unconditional coverage is NOT purchasable at this ceiling; "
            "coverage stays conditional on containment and the buffer becomes "
            "a risk decision")
        if highest_ack > highest_prod:
            row("capacity", "acceptance exceeds delivery by", BAD,
                highest_ack - highest_prod, 0,
                "the venue accepted subscriptions it did not serve")

        # Post-reconnect producer confirmation, where the run tested it.
        rec_ok = next((p.get("full_restoration") for p in
                       ((results or {}).get("phases") or [])
                       if str(p.get("phase", p.get("label", "")))
                       .endswith("_reconnect_after")), None)
        row("capacity", "capacity confirmed AFTER a reconnect",
            TODO if rec_ok is None else (OK if rec_ok else BAD),
            "not tested per rung" if rec_ok is None else
            ("producers resumed at the measured universe size" if rec_ok
             else "producers did NOT all resume"),
            "producer callbacks after reconnect at the capacity rung",
            "the probe runs after the reconnect phase and does not re-test "
            "each rung across a disconnect; capacity is confirmed at steady "
            "state only" if rec_ok is not None else "")

    # ---- 6. OPENING COVERAGE -------------------------------------------
    cov = (results or {}).get("opening_coverage") or {}
    st = cov.get("status")
    row("coverage", "canonical contained in provisional",
        OK if st == "CONTAINED" else BAD, st,
        "CONTAINED",
        "" if st == "CONTAINED" else
        "canonical symbols were not all pre-subscribed; those ticks were "
        "missed and the buffer policy needs revisiting")
    if cov.get("missing_from_provisional_count"):
        row("coverage", "canonical symbols missed at open", BAD,
            cov["missing_from_provisional_count"], 0)
    if universe:
        row("coverage", "measured universe size", OK,
            universe.get("measured_symbol_count"), "unassumed -- measured",
            "no expected count is asserted anywhere")

    silence = ((replay or {}).get("analysis") or {}).get("coverage") or {}
    if silence:
        row("coverage", "active / low-liquidity / never-produced", OK,
            f"{silence.get('active')} / {silence.get('low_liquidity')} / "
            f"{silence.get('never_produced')}",
            "silence is coverage, never loss",
            "confirm none of these were SUBSCRIPTION_FAILURE, which IS a failure")

    # ---- 7. RECONNECT ---------------------------------------------------
    rec = next((p for p in ((results or {}).get("phases") or [])
                if str(p.get("phase", p.get("label", ""))).endswith("_reconnect_after")), None)
    if rec is None:
        row("reconnect", "producer resumption", TODO, "phase absent", "present")
    else:
        full = rec.get("full_restoration")
        row("reconnect", "every prior producer resumed",
            OK if full else BAD,
            f"{rec.get('resumed_count')}/{rec.get('producers_before_reconnect')}",
            "all", "" if full else
            "some symbols producing before the disconnect never resumed; the "
            "runtime's reconnect assumption does not hold")
        row("reconnect", "time to first tick after resubscribe", OK,
            rec.get("time_to_first_tick_s"), "unassumed")
        if rec.get("baseline_was_vacuous"):
            row("reconnect", "baseline had producers", BAD, "vacuous",
                "non-empty", "restoration was never actually tested")

    # ---- 8. RUNTIME ASSUMPTIONS THE RUN CANNOT SETTLE -------------------
    row("still open", "float store retirement", TODO, "not attempted",
        "after a reachability test shows no enabled consumer reads latest()")
    row("still open", "bid/ask/OI/volume/depth decision policy", TODO,
        "not defined", "define per decision, only for fields marked OK above")
    row("still open", "MarketDataAdapter quote_source wiring", TODO,
        "not wired", "REST remains declared; ticks populate OptionLeg")
    row("still open", "Bujji runtime tick receipt", TODO,
        "unproven -- Gate 1 measures the HARNESS",
        "a separate runtime measurement is required",
        "a Gate 1 PASS says nothing about whether the Bujji runner receives "
        "these ticks")

    # ---- render ---------------------------------------------------------
    width = {"section": 12, "item": 42, "status": 8}
    current = None
    icons = {OK: "[ok]", WARN: "[--]", BAD: "[!!]", TODO: "[..]"}
    print("GATE 1 RECONCILIATION -- measured evidence vs branch assumptions")
    print(f"  session : {sess}")
    print(f"  branch  : {args.branch}")
    if run_summary:
        print(f"  run     : {run_summary.get('result_category')} "
              f"({run_summary.get('finished_ist', '')[:19]})")
    print()
    for r in _rows:
        if r["section"] != current:
            current = r["section"]
            print(f"\n{current.upper()}")
        print(f"  {icons.get(r['status'], '[??]')} {r['item'][:width['item']]:<{width['item']}} "
              f"measured={str(r['measured'])[:46]}")
        if r["assumed"]:
            print(f"       {'':<{width['item']}} assumed={str(r['assumed'])[:46]}")
        if r["consequence"]:
            print(f"       -> {r['consequence']}")

    counts = {}
    for r in _rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    print("\n" + "-" * 72)
    print("  " + "   ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    if counts.get(BAD):
        print(f"\n  {counts[BAD]} item(s) need a decision before any field-dependent")
        print("  logic is written. None of them are implemented by this tool.")
    print("\n  READ-ONLY. Nothing was changed. This is the input to the field-policy")
    print("  decision, not the decision.")

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(
            {"session_dir": str(sess), "rows": _rows, "counts": counts},
            indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
