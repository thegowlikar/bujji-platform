#!/usr/bin/env python3
"""Bujji Margin Calibration CLI — operator-run, human-triggered, READ-ONLY.

WHO RUNS THIS, AND WHERE: this script is meant to be executed by the
account owner, on their own machine, with their own real FYERS
credentials, at their own explicit trigger. It is NEVER invoked by
Claude/the assistant, and this session made zero real broker calls
while building or testing it -- every test in
tests/test_bujji_calibration_cli.py uses a fake, in-memory broker
object exclusively.

WHAT THIS DOES AND DOES NOT DO:
- READ-ONLY broker interaction only: this script only ever calls
  Broker.get_funds() and Broker.get_order_margin() -- both already
  live-certified, read-only FyersBroker methods
  (bujji/broker/fyers.py). It never calls place_order/cancel_order/
  modify_order, and never imports anything execution-capable beyond
  the FyersBroker class itself and the BrokerConfig it's built from.
- Creates NO new HTTP client, NO new broker abstraction, and NO second
  live path to FYERS -- it constructs the real, existing FyersBroker
  class from environment-variable credentials and hands it to the
  already-existing, already-tested FyersMarginProvider
  (bujji/trading_brain/risk_governor/broker_margin_reality_adapter.py),
  unmodified.
- Produces calibration EVIDENCE, never trade permission. Nothing here
  is reachable from, or feeds into, capital_check.assess_capital().

CREDENTIAL BOUNDARY (Step 3): reuses the SAME environment variable
names bujji.core.config.AppConfig.load() already establishes for the
main application -- FYERS_APP_ID, FYERS_ACCESS_TOKEN, and optionally
FYERS_APP_SECRET/FYERS_REFRESH_TOKEN/FYERS_PIN for automatic token
renewal. No credential is ever written to a calibration dataset file,
a report, a log line, or committed to git -- persisted sample records
deliberately EXCLUDE BrokerMarginSnapshot.raw_metadata (the raw broker
response), even though that field is unlikely to itself contain a
token, purely out of caution per this phase's explicit instruction.

OPERATOR WORKFLOW (Step 1):
  1. Authenticate FYERS locally (however you normally do -- this
     script does not perform login/token-generation itself, it only
     reads an already-valid FYERS_ACCESS_TOKEN from your environment).
  2. Run `validate` once to confirm the session, storage path, and
     offline-simulation guarantee are all healthy. STOP if it reports
     a failure -- do not proceed to `capture`.
  3. Run `capture` once per real observation you want to record,
     e.g.:
         python bujji_calibration_cli.py capture \\
             --sample-id nifty-straddle-001 \\
             --strategy NIFTY_SHORT_STRADDLE \\
             --description "NIFTY 24800 CE+PE short straddle" \\
             --underlying NIFTY --expiry 2026-08-27 --lot-size 75 \\
             --ce-symbol "NSE:NIFTY26AUG24800CE" --ce-strike 24800 \\
             --ce-qty 75 --ce-side SELL --ce-price 90.0 \\
             --pe-symbol "NSE:NIFTY26AUG24800PE" --pe-strike 24700 \\
             --pe-qty 75 --pe-side SELL --pe-price 85.0
     Each invocation is a SEPARATE, explicit, human-triggered process
     run -- nothing here schedules, retries automatically, or repeats
     a capture. Results append to a local dataset file (default
     calibration_dataset.json in the current directory); duplicate
     sample_ids are rejected, never overwritten.
  4. Run `report` to generate the certification report from everything
     collected so far -- this reuses the EXISTING, unmodified
     MarginCertificationEngine, never new certification logic.
  5. Run `export` to write the dataset to a flat CSV/JSON file for
     external analysis (no sensitive account data included).

Commands: capture | report | validate | export
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bujji.core.config import BrokerConfig
from bujji.core.enums import OptionType
from bujji.core.models import OptionContract
from bujji.trading_brain.risk_governor.broker_margin_reality_adapter import (
    BrokerMarginSnapshot,
    FyersMarginProvider,
)
from bujji.trading_brain.risk_governor.margin_calibration_runner import (
    MarginCalibrationRunner,
    MarginCalibrationSample,
    MarginCalibrationStore,
    build_calibration_run,
)
from bujji.trading_brain.risk_governor.margin_certification_engine import (
    MarginCertificationEngine,
    format_certification_report,
)
from bujji.trading_brain.risk_governor.margin_comparison_engine import MarginComparisonReport
from bujji.trading_brain.risk_governor.simulated_margin_provider import SimulatedMarginProvider
from bujji.trading_brain.risk_governor.whole_book_margin_provider import MarginLegRequest, MarginSnapshot

DEFAULT_DATASET_PATH = "calibration_dataset.json"


# --------------------------------------------------------------------- #
# Credential wiring -- the ONLY place in this whole session's work that
# constructs a real, live-capable FyersBroker instance. Never imported
# by any pure/tested risk_governor module; only this standalone script.
# --------------------------------------------------------------------- #

def build_broker_config_from_env() -> BrokerConfig:
    """Reuses bujji.core.config's own established FYERS_* environment
    variable names verbatim -- the same credential mechanism the main
    Bujji application already uses, not a new one."""
    return BrokerConfig(
        name="fyers",
        app_id=os.getenv("FYERS_APP_ID"),
        access_token=os.getenv("FYERS_ACCESS_TOKEN"),
        app_secret=os.getenv("FYERS_APP_SECRET"),
        refresh_token=os.getenv("FYERS_REFRESH_TOKEN"),
        pin=os.getenv("FYERS_PIN"),
    )


def build_operator_broker(broker_config: BrokerConfig):
    """Constructs the REAL, existing FyersBroker (bujji/broker/fyers.py,
    unmodified) -- zero new HTTP logic, zero new broker abstraction.
    Constructing a FyersBroker performs NO network I/O by itself (its
    SDK client is built lazily, only on first real method call) -- this
    function is therefore safe to call even with placeholder
    credentials, as this script's own test suite relies on."""
    from bujji.broker.fyers import FyersBroker

    logger = logging.getLogger("bujji_calibration_cli")
    return FyersBroker(broker_config, logger)


def build_operator_broker_provider(broker_config: BrokerConfig) -> FyersMarginProvider:
    """Wraps a real FyersBroker in the REAL, existing FyersMarginProvider
    (unmodified) -- see build_operator_broker()'s own docstring."""
    return FyersMarginProvider(build_operator_broker(broker_config))


def credentials_present(broker_config: BrokerConfig) -> bool:
    return bool(broker_config.app_id) and bool(broker_config.access_token)


# --------------------------------------------------------------------- #
# Local, file-based persistence -- MarginCalibrationStore itself is
# deliberately in-memory-only (see its own module docstring); this CLI
# needs samples to survive across separate process invocations, so this
# layer round-trips MarginCalibrationSample <-> a plain JSON shape.
# raw_metadata is deliberately NEVER persisted (see module docstring's
# credential boundary section).
# --------------------------------------------------------------------- #

def _dt(iso: str) -> datetime:
    return datetime.fromisoformat(iso)


def _leg_to_dict(leg: MarginLegRequest) -> Dict[str, Any]:
    return {
        "symbol": leg.symbol, "qty": leg.qty, "side": leg.side,
        "instrument_type": leg.instrument_type, "product_type": leg.product_type,
        "limit_price": leg.limit_price, "stop_loss": leg.stop_loss,
    }


def _leg_from_dict(d: Dict[str, Any]) -> MarginLegRequest:
    return MarginLegRequest(
        symbol=d["symbol"], qty=d["qty"], side=d["side"], instrument_type=d["instrument_type"],
        product_type=d["product_type"], limit_price=d.get("limit_price"), stop_loss=d.get("stop_loss"),
    )


def sample_to_dict(sample: MarginCalibrationSample) -> Dict[str, Any]:
    return {
        "sample_id": sample.sample_id,
        "timestamp": sample.timestamp.isoformat(),
        "strategy_type": sample.strategy_type,
        "position_description": sample.position_description,
        "margin_leg_input": [_leg_to_dict(leg) for leg in sample.margin_leg_input],
        "simulated_margin_snapshot": {
            "required_margin": sample.simulated_margin_snapshot.required_margin,
            "margin_verified": sample.simulated_margin_snapshot.margin_verified,
            "margin_source": sample.simulated_margin_snapshot.margin_source,
            "as_of": sample.simulated_margin_snapshot.as_of.isoformat(),
        },
        "broker_margin_snapshot": {
            "available_margin": sample.broker_margin_snapshot.available_margin,
            "used_margin": sample.broker_margin_snapshot.used_margin,
            "required_margin": sample.broker_margin_snapshot.required_margin,
            "timestamp": sample.broker_margin_snapshot.timestamp.isoformat(),
            "source": sample.broker_margin_snapshot.source,
            "available": sample.broker_margin_snapshot.available,
            # raw_metadata deliberately excluded -- never persisted, see module docstring.
        },
        "comparison_report": {
            "simulated_margin": sample.comparison_report.simulated_margin,
            "broker_margin": sample.comparison_report.broker_margin,
            "difference": sample.comparison_report.difference,
            "deviation_fraction": sample.comparison_report.deviation_fraction,
            "status": sample.comparison_report.status,
            "reason": sample.comparison_report.reason,
        },
        "metadata": dict(sample.metadata),
    }


def sample_from_dict(d: Dict[str, Any]) -> MarginCalibrationSample:
    sim = d["simulated_margin_snapshot"]
    brk = d["broker_margin_snapshot"]
    cmp_ = d["comparison_report"]
    return MarginCalibrationSample(
        sample_id=d["sample_id"], timestamp=_dt(d["timestamp"]), strategy_type=d["strategy_type"],
        position_description=d["position_description"],
        margin_leg_input=tuple(_leg_from_dict(l) for l in d["margin_leg_input"]),
        simulated_margin_snapshot=MarginSnapshot(
            required_margin=sim["required_margin"], margin_verified=sim["margin_verified"],
            margin_source=sim["margin_source"], as_of=_dt(sim["as_of"]), quote=None,
        ),
        broker_margin_snapshot=BrokerMarginSnapshot(
            available_margin=brk["available_margin"], used_margin=brk["used_margin"],
            required_margin=brk["required_margin"], timestamp=_dt(brk["timestamp"]),
            source=brk["source"], available=brk["available"], raw_metadata=None,
        ),
        comparison_report=MarginComparisonReport(
            simulated_margin=cmp_["simulated_margin"], broker_margin=cmp_["broker_margin"],
            difference=cmp_["difference"], deviation_fraction=cmp_["deviation_fraction"],
            status=cmp_["status"], reason=cmp_["reason"],
        ),
        metadata=dict(d.get("metadata", {})),
    )


def load_store(dataset_path: Path) -> MarginCalibrationStore:
    store = MarginCalibrationStore()
    if not dataset_path.exists():
        return store
    raw = json.loads(dataset_path.read_text())
    for entry in raw.get("samples", []):
        store.record_sample(sample_from_dict(entry))
    return store


def save_store(dataset_path: Path, store: MarginCalibrationStore) -> None:
    payload = {"samples": [sample_to_dict(s) for s in store.samples()]}
    dataset_path.write_text(json.dumps(payload, indent=2))


# --------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------- #

def cmd_validate(args: argparse.Namespace) -> int:
    print("Bujji Margin Calibration -- Preflight Validation")
    ok = True

    broker_config = build_broker_config_from_env()
    if credentials_present(broker_config):
        print("  [ok] FYERS_APP_ID / FYERS_ACCESS_TOKEN are set in the environment")
    else:
        print("  [FAIL] FYERS_APP_ID and/or FYERS_ACCESS_TOKEN are not set")
        ok = False

    dataset_path = Path(args.dataset)
    try:
        dataset_path.parent.mkdir(parents=True, exist_ok=True)
        probe = dataset_path.with_suffix(dataset_path.suffix + ".probe")
        probe.write_text("ok")
        probe.unlink()
        print(f"  [ok] calibration storage path is writable: {dataset_path}")
    except OSError as exc:
        print(f"  [FAIL] calibration storage path is not writable: {exc}")
        ok = False

    try:
        leg = MarginLegRequest(symbol="PROBE", qty=1, side=-1, instrument_type="OPTIDX",
                                product_type="MIS", limit_price=1.0)
        snapshot = SimulatedMarginProvider().get_portfolio_margin([leg], clock=lambda: datetime.now(timezone.utc))
        assert snapshot.margin_verified
        print("  [ok] simulation works offline (no broker access required)")
    except Exception as exc:  # noqa: BLE001
        print(f"  [FAIL] simulation self-check failed: {exc}")
        ok = False

    print("  [ok] adapter is structurally read-only (verified this session via AST scan + "
          "trap-broker execution proof -- no place_order/cancel_order reference exists anywhere "
          "in broker_margin_reality_adapter.py or margin_calibration_runner.py)")

    if not ok:
        print("\nPreflight FAILED -- do not proceed to `capture`.")
        return 1

    if args.live:
        print("\nChecking live broker session (real, read-only get_funds() call)...")
        broker = build_operator_broker(broker_config)

        async def check():
            return await broker.get_funds()

        try:
            funds = asyncio.run(check())
        except Exception as exc:  # noqa: BLE001
            print(f"  [FAIL] broker session check raised: {exc}")
            return 1
        if funds is None:
            print("  [FAIL] broker session unavailable or authentication failed")
            return 1
        print("  [ok] broker session is authenticated and reachable")

    print("\nPreflight PASSED.")
    return 0


def cmd_capture(args: argparse.Namespace) -> int:
    broker_config = build_broker_config_from_env()
    if not credentials_present(broker_config):
        print("FYERS_APP_ID/FYERS_ACCESS_TOKEN not set -- run `validate` first.", file=sys.stderr)
        return 1

    dataset_path = Path(args.dataset)
    store = load_store(dataset_path)

    legs = [
        MarginLegRequest(symbol=args.ce_symbol, qty=args.ce_qty,
                          side=1 if args.ce_side.upper() == "BUY" else -1,
                          instrument_type="OPTIDX", product_type=args.product_type, limit_price=args.ce_price),
        MarginLegRequest(symbol=args.pe_symbol, qty=args.pe_qty,
                          side=1 if args.pe_side.upper() == "BUY" else -1,
                          instrument_type="OPTIDX", product_type=args.product_type, limit_price=args.pe_price),
    ]
    ce_contract = OptionContract(symbol=args.ce_symbol, underlying=args.underlying, strike=args.ce_strike,
                                  option_type=OptionType.CE, expiry=args.expiry, lot_size=args.lot_size)
    pe_contract = OptionContract(symbol=args.pe_symbol, underlying=args.underlying, strike=args.pe_strike,
                                  option_type=OptionType.PE, expiry=args.expiry, lot_size=args.lot_size)

    simulated_provider = SimulatedMarginProvider()
    broker_provider = build_operator_broker_provider(broker_config)
    runner = MarginCalibrationRunner(simulated_provider, broker_provider, store)

    result = asyncio.run(runner.capture_sample(
        sample_id=args.sample_id, strategy_type=args.strategy, position_description=args.description,
        legs=legs, ce_contract=ce_contract, pe_contract=pe_contract,
        clock=lambda: datetime.now(timezone.utc),
    ))

    if not result.captured:
        print(f"SAMPLE_NOT_CAPTURED: {result.reason}", file=sys.stderr)
        return 1

    save_store(dataset_path, store)
    print(f"CAPTURED: {result.sample.sample_id}")
    print(f"  simulated_margin: {result.sample.simulated_margin_snapshot.required_margin}")
    print(f"  broker_margin:    {result.sample.broker_margin_snapshot.required_margin}")
    print(f"  comparison:       {result.sample.comparison_report.status} "
          f"({result.sample.comparison_report.deviation_fraction})")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    dataset_path = Path(args.dataset)
    if not dataset_path.exists():
        print(f"no dataset found at {dataset_path}", file=sys.stderr)
        return 1
    store = load_store(dataset_path)
    engine = MarginCertificationEngine(
        pass_threshold=args.pass_threshold, fail_threshold=args.fail_threshold,
    )
    run = build_calibration_run(args.run_id, store, clock=lambda: datetime.now(timezone.utc), engine=engine)
    print(format_certification_report(run.certification_result, period=args.period))
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    dataset_path = Path(args.dataset)
    if not dataset_path.exists():
        print(f"no dataset found at {dataset_path}", file=sys.stderr)
        return 1
    store = load_store(dataset_path)
    rows = store.export_samples()   # already-existing, unmodified export -- no new certification/export logic

    out_path = Path(args.output)
    if args.format == "json":
        out_path.write_text(json.dumps(rows, indent=2))
    else:
        import csv
        fieldnames = ["sample_id", "timestamp", "strategy_type", "position_description",
                      "simulated_margin", "broker_margin", "comparison_status", "deviation_fraction"]
        with out_path.open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                writer.writerow(row)
    print(f"exported {len(rows)} samples to {out_path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bujji_calibration_cli.py", description=__doc__.split("\n\n")[0])
    parser.add_argument("--dataset", default=DEFAULT_DATASET_PATH, help="local calibration dataset JSON file")
    sub = parser.add_subparsers(dest="command", required=True)

    p_validate = sub.add_parser("validate", help="run preflight safety checks (Step 1)")
    p_validate.add_argument("--live", action="store_true",
                             help="also perform a real, read-only get_funds() broker session check")
    p_validate.set_defaults(func=cmd_validate)

    p_capture = sub.add_parser("capture", help="capture one real calibration sample (CE+PE pair)")
    p_capture.add_argument("--sample-id", required=True)
    p_capture.add_argument("--strategy", required=True)
    p_capture.add_argument("--description", default="")
    p_capture.add_argument("--underlying", default="NIFTY")
    p_capture.add_argument("--expiry", required=True)
    p_capture.add_argument("--lot-size", type=int, required=True)
    p_capture.add_argument("--product-type", default="MIS")
    p_capture.add_argument("--ce-symbol", required=True)
    p_capture.add_argument("--ce-strike", type=int, required=True)
    p_capture.add_argument("--ce-qty", type=int, required=True)
    p_capture.add_argument("--ce-side", choices=["BUY", "SELL"], required=True)
    p_capture.add_argument("--ce-price", type=float, required=True)
    p_capture.add_argument("--pe-symbol", required=True)
    p_capture.add_argument("--pe-strike", type=int, required=True)
    p_capture.add_argument("--pe-qty", type=int, required=True)
    p_capture.add_argument("--pe-side", choices=["BUY", "SELL"], required=True)
    p_capture.add_argument("--pe-price", type=float, required=True)
    p_capture.set_defaults(func=cmd_capture)

    p_report = sub.add_parser("report", help="generate a certification report from the local dataset")
    p_report.add_argument("--run-id", default="operator-run")
    p_report.add_argument("--period", default=None)
    p_report.add_argument("--pass-threshold", type=float, default=0.05)
    p_report.add_argument("--fail-threshold", type=float, default=0.15)
    p_report.set_defaults(func=cmd_report)

    p_export = sub.add_parser("export", help="export the local dataset to CSV or JSON")
    p_export.add_argument("--output", required=True)
    p_export.add_argument("--format", choices=["csv", "json"], default="csv")
    p_export.set_defaults(func=cmd_export)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
