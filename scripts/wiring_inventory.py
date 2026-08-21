"""Print the wiring inventory: what runs, what only exists.

  python scripts/wiring_inventory.py
  python scripts/wiring_inventory.py --max-undeclared 40   # ratchet in CI
  python scripts/wiring_inventory.py --json /tmp/wiring.json

Read-only. Parses source with `ast` and never imports the packages it
audits, so scanning executes none of the code under review.

The headline number is UNDECLARED DORMANT packages: code that exists, is
reachable from no scheduled systemd entrypoint, and never declared itself
dormant. Use --max-undeclared to ratchet it downward over time; it exits
non-zero when the count exceeds the ceiling you set.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from bujji.wiring_inventory.inventory import build_inventory  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(REPO_ROOT))
    ap.add_argument("--json", default=None)
    ap.add_argument("--max-undeclared", type=int, default=None,
                    help="exit non-zero if undeclared dormant packages exceed this")
    ap.add_argument("--quiet", action="store_true", help="headline number only")
    args = ap.parse_args()

    root = Path(args.root)
    report = build_inventory(root)

    if args.quiet:
        print(report.headline)
    else:
        print(f"ENTRYPOINTS (from deploy/*.service ExecStart): {len(report.entrypoints)}")
        for name in report.entrypoints:
            print(f"  {name}")

        live = [p for p in report.packages if p.reachable]
        print(f"\nPACKAGES: {len(report.packages)}   "
              f"reachable={len(live)}   dormant={len(report.dormant)}")

        if report.dormant:
            print("\nDORMANT -- reachable from no scheduled entrypoint")
            width = max(len(p.package) for p in report.dormant)
            for p in sorted(report.dormant, key=lambda x: (-x.modules, x.package)):
                flag = "declared" if p.dormant_reason else "UNDECLARED"
                store = "  [defines a store -- it can hold no rows]" if p.persists_to_sqlite else ""
                print(f"  {p.package:<{width}}  modules={p.modules:<3} "
                      f"importers={p.importers:<3} {flag}{store}")
                if p.dormant_reason:
                    print(f"  {'':<{width}}    reason: {p.dormant_reason}")

        if report.orphan_consumers:
            print("\nORPHAN CONSUMERS -- live code reading a dormant package")
            for consumer, dormant in report.orphan_consumers:
                print(f"  {consumer}  ->  {dormant}")

        print(f"\nHEADLINE undeclared dormant packages: {report.headline}")

    if args.json:
        Path(args.json).write_text(json.dumps({
            "headline_undeclared_dormant": report.headline,
            "entrypoints": report.entrypoints,
            "orphan_consumers": [list(o) for o in report.orphan_consumers],
            "packages": [{
                "package": p.package, "modules": p.modules, "importers": p.importers,
                "reachable": p.reachable, "dormant_reason": p.dormant_reason,
                "persists_to_sqlite": p.persists_to_sqlite,
            } for p in report.packages],
        }, indent=2))

    if args.max_undeclared is not None and report.headline > args.max_undeclared:
        print(f"\nFAIL: {report.headline} undeclared dormant packages "
              f"exceeds the ceiling of {args.max_undeclared}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
