"""Can FYERS actually accept enough subscriptions for zero-gap coverage?

NOBODY MAY PROMISE ZERO-GAP UNTIL THIS IS ANSWERED. Two candidate designs
exist, and both are subscription-bound:

  A) buffer the provisional bands by a justified maximum opening move
  B) subscribe the canonical ROLE EXPIRIES IN FULL

(B) is the stronger claim and, measured against the real master, the cheaper
one than it sounds: only the three role expiries can ever enter the canonical
universe, because `select_expiries()` takes `as_of` and NOT spot. Expiry roles
are therefore fixed before the bell; only the strike band moves with the open.
Covering those expiries completely is unconditional with respect to
opening-move size -- there is no move large enough to reach a contract that
was never eligible.

This probe measures what the venue will actually accept, in ascending steps,
and stops at the first refusal. It is READ-ONLY: it subscribes to market data
and places nothing.

RUN IT DURING MARKET HOURS on a day already given to measurement. Its result
is capacity evidence, not a Gate 1 verdict.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols-file", required=True,
                    help="JSON with an 'instruments' list to draw from")
    ap.add_argument("--steps", default="250,500,750,1000,1250,1500")
    ap.add_argument("--dwell-seconds", type=int, default=45)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    doc = json.loads(Path(args.symbols_file).read_text())
    pool = [i["symbol"] for i in doc["instruments"]]
    steps = [int(x) for x in args.steps.split(",") if int(x) <= len(pool)]
    if not steps:
        raise SystemExit(f"no step fits the {len(pool)}-symbol pool provided")

    app_id, token = os.environ.get("FYERS_APP_ID"), os.environ.get("FYERS_ACCESS_TOKEN")
    if not app_id or not token:
        raise SystemExit("credentials absent from the environment")

    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    from fyers_apiv3.FyersWebsocket import data_ws

    seen: dict = {}
    errors: list = []
    lock = threading.Lock()
    state = {"connects": 0}

    def on_msg(msg):
        sym = msg.get("symbol")
        if sym and msg.get("ltp") is not None:
            with lock:
                seen.setdefault(sym, time.time())

    sock = data_ws.FyersDataSocket(
        access_token=f"{app_id}:{token}", log_path=str(out), litemode=False,
        write_to_file=False, reconnect=True,
        on_connect=lambda: state.__setitem__("connects", state["connects"] + 1),
        on_close=lambda m: None,
        on_error=lambda m: errors.append({"t": time.time(), "msg": str(m)[:300]}),
        on_message=on_msg)
    threading.Thread(target=sock.connect, daemon=True).start()
    for _ in range(40):
        if state["connects"]:
            break
        time.sleep(0.5)
    if not state["connects"]:
        raise SystemExit("no websocket connection")

    results = {"probe": "GATE1_SUBSCRIPTION_CAPACITY",
               "at": datetime.now(timezone.utc).isoformat(),
               "pool_size": len(pool), "steps": steps, "rungs": []}
    print(f"pool {len(pool)} symbols; steps {steps}\n", flush=True)

    for n in steps:
        before_err = len(errors)
        with lock:
            seen.clear()
        t0 = time.time()
        try:
            sock.subscribe(symbols=pool[:n], data_type="SymbolUpdate")
            submitted = True
        except Exception as exc:
            submitted = False
            errors.append({"t": time.time(), "msg": f"subscribe({n}): {exc}"})
        time.sleep(args.dwell_seconds)
        with lock:
            producing = len(seen)
        rung = {
            "requested": n, "submit_ok": submitted,
            "symbols_producing": producing,
            "coverage_pct": round(100.0 * producing / n, 1) if n else None,
            "new_errors": len(errors) - before_err,
            "elapsed_s": round(time.time() - t0, 1),
        }
        results["rungs"].append(rung)
        print(json.dumps(rung), flush=True)
        # A rung that submits but produces almost nothing is the venue quietly
        # capping us. Treat it as the ceiling and stop, rather than climbing
        # further and reporting numbers that describe a truncated subscription.
        if not submitted or (producing == 0 and n > steps[0]):
            print(f"ceiling reached at {n}", flush=True)
            break

    try:
        sock.close_connection()
    except Exception:
        pass
    results["errors"] = errors[:50]
    ok = [r for r in results["rungs"] if r["submit_ok"] and r["symbols_producing"]]
    results["highest_accepted"] = max((r["requested"] for r in ok), default=0)
    results["conclusion"] = (
        f"highest subscription count that produced data: "
        f"{results['highest_accepted']}. Zero-gap via full role-expiry coverage "
        f"needs ~1465 for NIFTY; buffered bands need far fewer. This is "
        f"capacity evidence for that decision, not permission to promise it.")
    (out / "capacity_probe.json").write_text(json.dumps(results, indent=1, sort_keys=True))
    print(f"\n{results['conclusion']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
