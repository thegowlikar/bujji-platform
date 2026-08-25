"""Read the observed spot through the EXECUTION-NEUTERED data path. Nothing else.

WHY THIS IS SAFE, AND WHY THAT IS VERIFIED RATHER THAN ASSERTED.
`disable_live_execution()` replaces place_order, modify_order, cancel_order,
get_open_positions and get_order with stubs that RAISE. It is applied here
immediately on construction, before the object is used for anything, and this
module then PROVES the neutering held before it will return a price. If any
execution method is still callable, no spot is produced and the run fails --
a price is not worth obtaining through an object that could also trade.

NO PROXY, NO MANUAL INPUT, NO GUESS. A median-strike proxy or a hand-typed
level centres the capture band on the wrong strikes and quietly under-measures
the feed. If the read-only path cannot produce a real quote, this exits
non-zero and the measurement does not happen.

Prints ONLY the spot and its provenance. No credential-adjacent value is read,
logged, or emitted.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
EXECUTION_SURFACE = ("place_order", "modify_order", "cancel_order",
                     "get_open_positions", "get_order")


async def read_spot(code_root: str, underlying: str, config_path: str) -> dict:
    sys.path.insert(0, code_root)
    from bujji.broker.errors import LiveExecutionDisabledError
    from bujji.broker.fyers import FyersBroker
    from bujji.broker.guard import disable_live_execution
    from bujji.core.config import AppConfig

    log = logging.getLogger("gate1-spot")
    logging.basicConfig(level=logging.WARNING)

    # Same config the trading unit uses; secrets come from the
    # environment, which this module never reads or emits.
    config = AppConfig.load(config_path)
    # NEUTER FIRST. There is no window in which this object is trade-capable.
    broker = disable_live_execution(FyersBroker(config.broker, log))

    # PROVE IT, before trusting the object with anything. A guard that silently
    # failed to apply would leave an order-capable broker in an unattended
    # measurement run -- exactly the thing the whole session forbids.
    proof = {}
    for name in EXECUTION_SURFACE:
        if not hasattr(broker, name):
            proof[name] = "absent"
            continue
        try:
            await getattr(broker, name)()
        except LiveExecutionDisabledError:
            proof[name] = "raises LiveExecutionDisabledError"
        except TypeError:
            # Reached the stub but with the wrong arity: still the stub, since
            # the real methods accept these calls. Re-test explicitly.
            try:
                await getattr(broker, name)(None)
                proof[name] = "DID NOT RAISE"
            except LiveExecutionDisabledError:
                proof[name] = "raises LiveExecutionDisabledError"
            except Exception as exc:
                proof[name] = f"raised {type(exc).__name__}"
        except Exception as exc:
            proof[name] = f"raised {type(exc).__name__}"

    unguarded = [n for n, v in proof.items()
                 if "LiveExecutionDisabledError" not in v and v != "absent"]
    if unguarded:
        raise SystemExit(
            f"REFUSING TO READ A PRICE: the execution guard did not hold for "
            f"{unguarded}. An unattended measurement will not touch a broker "
            f"object that might still trade.")

    spot = await broker.get_spot(underlying)
    if not isinstance(spot, (int, float)) or not (spot > 0):
        raise SystemExit(f"read-only spot source returned {spot!r} -- refusing "
                         f"to build a universe around a value that is not a price")

    return {
        "spot": float(spot),
        "underlying": underlying,
        "observed_at_ist": datetime.now(IST).isoformat(),
        "source": "FyersBroker.get_spot via disable_live_execution "
                  "(read-only; order surface raises)",
        "execution_guard_proof": proof,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--code-root", default="/opt/bujji/work-m4")
    ap.add_argument("--underlying", default="NIFTY")
    ap.add_argument("--config",
                    default="/opt/bujji/app/config/options_os_paper_trading.yaml")
    ap.add_argument("--out", default=None, help="write the provenance JSON here")
    args = ap.parse_args()
    try:
        result = asyncio.run(read_spot(args.code_root, args.underlying,
                                       args.config))
    except SystemExit:
        raise
    except Exception as exc:
        print(f"SPOT READ FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    if args.out:
        Path(args.out).write_text(json.dumps(result, indent=1, sort_keys=True))
    # stdout is the spot alone, so a caller can capture it without parsing.
    print(result["spot"])
    print(json.dumps({k: v for k, v in result.items() if k != "spot"}),
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
