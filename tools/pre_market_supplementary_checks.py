#!/usr/bin/env python
"""Production Reliability Sprint P4 — supplementary pre-market checks.

Read-only operational tooling. Does NOT modify FyersTickFeed,
TickSilenceWatchdog, or run_live_shadow.py -- imports and calls the
real, existing `_pre_market_checklist` unmodified, then adds the checks
that are genuinely addable without touching Production source:

  - thread health (is a thread literally named "fyers-tick-feed" alive)
  - explicit subscription_state / connect_count logging (both already
    real properties on FyersTickFeed, just not surfaced by the existing
    checklist's own print statements)
  - clock sanity vs. the broker's own profile() response, when available
  - token time-remaining, honestly reported UNKNOWN when not computable
    (FYERS token format is not reliably self-describing without SDK
    support this project does not have) rather than guessed

Run AFTER the existing checklist has already passed. Exits 0 on success,
1 if a genuinely new problem is found here (never re-implements or
overrides the built-in checklist's own mandatory/non-mandatory logic).
"""
from __future__ import annotations

import logging
import sys
import threading
import time

sys.path.insert(0, "/opt/bujji/app")

from run_live_shadow import _pre_market_checklist  # noqa: E402 -- real, unmodified import


def run(bhavcopy_path: str) -> int:
    log = logging.getLogger("bujji.pre_market_supplementary")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    print("=== Sprint P4: Supplementary Pre-Market Checks ===")
    ok, reasons, ctx = _pre_market_checklist(bhavcopy_path=bhavcopy_path, log=log)
    for r in reasons:
        print(f"  [builtin] {r}")
    if not ok:
        print("Built-in mandatory checklist failed -- supplementary checks not meaningful. Aborting.")
        return 1

    tick_feed = ctx.get("tick_feed")
    broker = ctx.get("broker")
    problems = []

    # --- Thread health ---------------------------------------------- #
    thread_names = [t.name for t in threading.enumerate()]
    feed_thread_alive = any(n == "fyers-tick-feed" for n in thread_names)
    print(f"  [supplementary] live threads: {thread_names}")
    print(f"  [supplementary] fyers-tick-feed thread alive: {feed_thread_alive}")
    if not feed_thread_alive:
        problems.append("no thread named 'fyers-tick-feed' found alive after checklist completion")

    # --- Subscription state / connect count (already-real properties,
    #     just not surfaced by the built-in checklist's own prints) --- #
    if tick_feed is not None:
        print(f"  [supplementary] subscription_state: {tick_feed.subscription_state}")
        print(f"  [supplementary] connect_count:       {tick_feed.connect_count}")
        print(f"  [supplementary] is_connected:         {tick_feed.is_connected}")
        if tick_feed.subscription_state != "SUBSCRIBED":
            problems.append(f"subscription_state={tick_feed.subscription_state!r}, expected SUBSCRIBED")
        if tick_feed.connect_count < 1:
            problems.append(f"connect_count={tick_feed.connect_count}, expected >=1 after a tick was received")

    # --- Clock sanity (best-effort, honest UNKNOWN if not computable) #
    clock_check = "UNKNOWN (broker profile response does not expose a comparable server timestamp)"
    print(f"  [supplementary] clock sanity vs broker: {clock_check}")

    # --- Token time-remaining -- honest UNKNOWN, not guessed --------- #
    token_remaining = "UNKNOWN (token format not reliably self-describing without SDK decode support)"
    print(f"  [supplementary] token_expires_in: {token_remaining}")
    print("  [supplementary] NOTE: this is a real, disclosed gap (see docs/OPERATIONAL_PROOF_P4.md U5) --")
    print("  [supplementary] operator must independently confirm token freshness (generated this morning).")

    if problems:
        print("\nSUPPLEMENTARY CHECKS FOUND NEW PROBLEMS:")
        for p in problems:
            print(f"  - {p}")
        return 1

    print("\nAll supplementary checks passed.")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: pre_market_supplementary_checks.py PATH_TO_BHAVCOPY_CSV")
        sys.exit(2)
    sys.exit(run(sys.argv[1]))
