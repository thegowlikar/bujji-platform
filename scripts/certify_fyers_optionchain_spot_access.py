#!/usr/bin/env python3
"""Certify that the option chain's underlying price IS NIFTY spot.

WHAT IS BEING CERTIFIED
-----------------------
`capture_options_reality_session._underlying_spot()` reads the underlying from
the chain response's sentinel row (strike_price == -1). That value has always
been used to centre the capture band; storing it as a SPOT observation is a
different claim -- that it is a faithful spot price the intelligence layer may
reason from. `(direct_sdk_fyers_optionchain_reality, SPOT)` therefore needs its
own certification: the existing NIFTY_SPOT certification covers a DIFFERENT
access method (direct_sdk_fyers_broker_py) and the gate indexes on the exact
(instrument, access_method) pair.

HOW
---
N times, back to back: pull the chain and read the sentinel, then ask the
broker for spot through the already-certified path (`FyersBroker.get_spot`),
and compare. The two calls are one pacer slot apart (~0.12s), so genuine market
drift dominates the difference; a stale or synthetic underlying shows up as a
persistent or erratic gap instead.

This imports `_underlying_spot` FROM the capture script rather than
reimplementing it -- certifying a copy of the extraction would certify nothing
about the code that actually runs.

MARKET HOURS ONLY. A zero-movement comparison after close proves nothing about
freshness, so this refuses to run outside the session, like every other
certification script here.

WRITES CERTIFIED_AVAILABLE ONLY ON A CLEAN PASS. Any failure records the real
evidence and NOT_CERTIFIED; the gate then keeps refusing SPOT writes, which is
the correct outcome, not a problem to work around.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime
import json
import logging
import statistics
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from capture_options_reality_session import (  # noqa: E402
    ACCESS_METHOD, INDEX_SYMBOL, STRIKE_COUNT, UNDERLYING,
    _underlying_spot, now_ist, within_market_hours,
)

LOG = logging.getLogger("certify_optionchain_spot")

CERT_INSTRUMENT = "NIFTY_SPOT"        # CertificationGate's key for INSTRUMENT_SPOT
DEFAULT_SAMPLES = 5
DEFAULT_SPACING_SECONDS = 10.0
# 5 bps ~= 12 points at 24,400. Wide enough to absorb real drift across one
# pacer slot, tight enough that a stale or unrelated underlying fails.
DEFAULT_TOLERANCE_BPS = 5.0

OUT_DIR = REPO_ROOT / "data_certification"


def assess(samples: list, tolerance_bps: float) -> dict:
    """Pure verdict over collected samples -- no I/O, so it is testable."""
    usable = [s for s in samples if s.get("chain_spot") is not None
              and s.get("broker_spot") is not None]
    for s in usable:
        s["abs_diff"] = abs(s["chain_spot"] - s["broker_spot"])
        s["diff_bps"] = (s["abs_diff"] / s["broker_spot"]) * 10_000.0
        s["within_tolerance"] = s["diff_bps"] <= tolerance_bps

    issues = []
    if not usable:
        issues.append("no sample produced both a chain underlying and a broker spot")
    if len(usable) < len(samples):
        issues.append(f"{len(samples) - len(usable)} of {len(samples)} samples incomplete")
    failed = [s for s in usable if not s["within_tolerance"]]
    if failed:
        issues.append(
            f"{len(failed)} of {len(usable)} samples exceeded {tolerance_bps} bps "
            f"(worst {max(s['diff_bps'] for s in failed):.2f} bps)")

    bps = [s["diff_bps"] for s in usable]
    return {
        "validation_result": "CERTIFIED_AVAILABLE" if (usable and not issues) else "NOT_CERTIFIED",
        "samples_requested": len(samples),
        "samples_usable": len(usable),
        "tolerance_bps": tolerance_bps,
        "max_diff_bps": max(bps) if bps else None,
        "mean_diff_bps": statistics.fmean(bps) if bps else None,
        "max_abs_diff_points": max(s["abs_diff"] for s in usable) if usable else None,
        "issues": issues,
        "samples": usable,
    }


async def collect(broker, samples: int, spacing: float) -> list:
    out = []
    for i in range(samples):
        if i:
            await asyncio.sleep(spacing)
        at = now_ist().isoformat()
        chain_spot = broker_spot = None
        error = None
        try:
            raw = await broker._call("optionchain", symbol=INDEX_SYMBOL,
                                     strikecount=STRIKE_COUNT, timestamp="")
            data = raw.get("data") if isinstance(raw, dict) else None
            chain_spot = _underlying_spot(data) if data else None
            broker_spot = await broker.get_spot(UNDERLYING)
        except Exception as exc:  # noqa: BLE001
            error = f"{type(exc).__name__}: {exc}"
        LOG.info("sample %d/%d at %s: chain=%s broker=%s %s",
                 i + 1, samples, at[11:19], chain_spot, broker_spot, error or "")
        out.append({"at": at, "chain_spot": chain_spot,
                    "broker_spot": broker_spot, "error": error})
    return out


async def run(samples: int, spacing: float, tolerance_bps: float) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    started = now_ist()
    if not within_market_hours(started):
        LOG.error("Outside market hours (%s IST). A post-close comparison cannot "
                  "distinguish a live underlying from a frozen one -- refusing to "
                  "certify.", started.isoformat())
        return 1

    from bujji.broker.errors import AuthenticationError
    from bujji.broker.fyers import FyersBroker
    from bujji.core.config import AppConfig

    cfg = AppConfig.load("config/config.yaml").broker
    cfg.name = "fyers"
    broker = FyersBroker(cfg, LOG)
    try:
        await broker.connect()
    except AuthenticationError as exc:
        LOG.error("token invalid, aborting: %s", exc)
        return 3

    verdict = assess(await collect(broker, samples, spacing), tolerance_bps)

    artifact = {
        "timestamp": now_ist().isoformat(),
        "broker": "fyers",
        "access_method": ACCESS_METHOD,
        "instrument": CERT_INSTRUMENT,
        "symbol_requested": INDEX_SYMBOL,
        "claim": ("the option chain response's sentinel-strike (-1) ltp is a "
                  "faithful NIFTY spot price, equal within tolerance to "
                  "FyersBroker.get_spot() sampled at the same moment"),
        "extraction_function": "capture_options_reality_session._underlying_spot",
        **verdict,
        "limitations": [
            "Compares against FyersBroker.get_spot(), itself certified under "
            "access_method=direct_sdk_fyers_broker_py -- this certifies agreement "
            "with that source, not independent correctness of either.",
            "The chain and spot calls are sequential (one pacer slot apart), so a "
            "non-zero difference is expected and is not evidence of staleness on "
            "its own; the tolerance exists for exactly that reason.",
        ],
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"fyers_optionchain_spot_certification_{started:%Y%m%d}.json"
    out.write_text(json.dumps(artifact, indent=2) + "\n")

    LOG.info("")
    LOG.info("verdict: %s", verdict["validation_result"])
    LOG.info("  usable samples: %s/%s", verdict["samples_usable"], verdict["samples_requested"])
    if verdict["max_diff_bps"] is not None:
        LOG.info("  max diff: %.3f bps (%.2f points)   mean: %.3f bps",
                 verdict["max_diff_bps"], verdict["max_abs_diff_points"],
                 verdict["mean_diff_bps"])
    for issue in verdict["issues"]:
        LOG.info("  issue: %s", issue)
    LOG.info("  written: %s", out)
    if verdict["validation_result"] != "CERTIFIED_AVAILABLE":
        LOG.info("")
        LOG.info("  The gate will keep refusing SPOT writes. That is the correct")
        LOG.info("  outcome for this evidence -- do not hand-edit the artifact.")
        return 4
    LOG.info("")
    LOG.info("  (direct_sdk_fyers_optionchain_reality, SPOT) is now open. The next")
    LOG.info("  capture cycle re-reads the gate and starts storing spot -- no")
    LOG.info("  restart and no code change needed.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--samples", type=int, default=DEFAULT_SAMPLES)
    ap.add_argument("--spacing-seconds", type=float, default=DEFAULT_SPACING_SECONDS)
    ap.add_argument("--tolerance-bps", type=float, default=DEFAULT_TOLERANCE_BPS)
    a = ap.parse_args()
    return asyncio.run(run(a.samples, a.spacing_seconds, a.tolerance_bps))


if __name__ == "__main__":
    raise SystemExit(main())
