#!/usr/bin/env python3
"""Turn a sealed Gate 1 raw corpus into a table, without inventing columns.

READ-ONLY. Reads raw_*.jsonl and writes a CSV plus a summary beside it. It
never modifies the corpus and never touches the running harness.

WHY THE CORPUS IS NOT ALREADY TABULAR, and why this is a separate step. The
corpus is an append-only verbatim wire record: the SDK payload is stored
unmodified, frozen to a string inside the callback before anything derives
from it. A tabular writer would have to fix its columns before anyone knew
what the feed sends, and every field outside that schema would be dropped at
the moment of capture -- silently, and unrecoverably. Deriving the table
afterwards is lossless and repeatable; capturing into one is neither.

THE COLUMNS ARE MEASURED, NOT DECLARED. Payload columns are the union of keys
actually observed in the corpus, in frequency order. There is no expected-key
list here, and deliberately no reference to PROVISIONAL_SDK_FIELD_MAP: a table
that showed a column for every field Bujji HOPES to receive would report
absence and non-existence identically, which is the confusion the typed quote
path exists to prevent.

AN ABSENT FIELD IS AN EMPTY CELL, NEVER A ZERO. The summary reports per-key
fill rate so "the feed never sent this" is legible as such rather than
inferred from a column of zeros.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

# MARKET DATA IS IDENTIFIED STRUCTURALLY, NOT BY AN ALLOW-LIST.
#
# An earlier version listed the control frame types it knew about
# ({"cn","ful","sub","unsub","error"}) and treated everything else as market
# data. Run against the real corpus that misclassified a "lit" acknowledgement
# -- shape (code, message, s, type) -- as a price record, because "lit" was
# simply not on the list. An allow-list of control types has to be complete to
# be correct, and nobody can know it is complete before seeing the feed.
#
# A market-data record must identify an instrument. Frames without a `symbol`
# are transport or status, whatever they call themselves, and that test needs
# no prior knowledge of the vocabulary.
def is_market(payload: dict) -> bool:
    return isinstance(payload.get("symbol"), str) and bool(payload.get("symbol"))

PROVENANCE_COLUMNS = ["seq", "recv_ts", "recv_monotonic", "tid"]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def scan(path: Path):
    """One pass to learn the shape, so columns come from the data."""
    key_counts: Counter = Counter()
    kinds: dict = {}
    type_counts: Counter = Counter()
    shape_counts: Counter = Counter()
    symbols: set = set()
    total = malformed = control = market = 0
    seqs = []

    with open(path, "r", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            total += 1
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                malformed += 1
                continue
            payload = rec.get("payload")
            if not isinstance(payload, dict):
                malformed += 1
                continue
            if isinstance(rec.get("seq"), int):
                seqs.append(rec["seq"])
            ptype = payload.get("type")
            type_counts[ptype] += 1
            kinds[ptype] = "market" if is_market(payload) else "control"
            if not is_market(payload):
                control += 1
                continue
            market += 1
            shape_counts[tuple(sorted(payload.keys()))] += 1
            for k in payload:
                key_counts[k] += 1
            sym = payload.get("symbol")
            if isinstance(sym, str):
                symbols.add(sym)

    gaps = []
    if seqs:
        seen = set(seqs)
        lo, hi = min(seqs), max(seqs)
        # A hole in the sequence is ingestion loss and must be visible. The
        # harness counts drops itself; this is an independent read of the
        # same fact from the file alone.
        missing = (hi - lo + 1) - len(seen)
        if missing:
            gaps = [lo, hi, missing]

    return {
        "total_lines": total, "malformed": malformed,
        "control_frames": control, "market_records": market,
        "key_counts": key_counts, "type_counts": type_counts,
        "shape_counts": shape_counts, "symbols": symbols, "kinds": kinds,
        "seq_span": gaps,
    }


def write_table(path: Path, out_csv: Path, columns):
    written = 0
    with open(path, "r", errors="replace") as fh, \
            open(out_csv, "w", newline="") as out:
        w = csv.writer(out)
        w.writerow(PROVENANCE_COLUMNS + columns)
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            payload = rec.get("payload")
            if not isinstance(payload, dict):
                continue
            if not is_market(payload):
                continue
            row = [rec.get(c) for c in ("seq", "recv_ts", "recv_monotonic", "tid")]
            # MISSING IS EMPTY, NOT ZERO. csv writes None as "", which is the
            # honest rendering of a field the feed did not send.
            row += [payload.get(c) for c in columns]
            w.writerow(row)
            written += 1
    return written


def render_summary(path, info, columns, rows, out_csv, digest) -> str:
    m = info["market_records"]
    lines = [
        "=" * 72,
        f"GATE 1 CORPUS -> TABLE  ({path.name})",
        "=" * 72,
        f"  corpus sha256   {digest}",
        f"  lines read      {info['total_lines']:,}",
        f"  market records  {m:,}   -> {rows:,} table rows",
        f"  control frames  {info['control_frames']:,}  (excluded from the table, counted here)",
        f"  malformed       {info['malformed']:,}",
        f"  distinct symbols {len(info['symbols']):,}",
        "",
        "FIELD AVAILABILITY -- measured, not expected",
    ]
    if not m:
        lines.append("  (no market records; nothing to measure)")
    for k, c in info["key_counts"].most_common():
        # FLOORED, NOT ROUNDED. 25,025 of 25,026 rounds to 100.00% and then
        # reads as "every record", which is the one thing it is not. A field
        # that is absent even once must not display as universal.
        exact = (c == m)
        pct = 100.0 * c / m if m else 0.0
        shown = pct if exact else min(pct, 99.99)
        note = "" if exact else f"   <- absent on {m - c:,} record(s)"
        lines.append(f"  {k:<24} {c:>10,}  {shown:6.2f}%{note}")

    lines += ["", "PAYLOAD SHAPES"]
    for keys, c in info["shape_counts"].most_common(10):
        lines.append(f"  {c:>10,}  {', '.join(keys)}")

    kinds = info["kinds"]
    lines += ["", "FRAME TYPES"]
    for t, c in info["type_counts"].most_common(10):
        kind = kinds.get(t, "?")
        lines.append(f"  {str(t):<10} {c:>10,}  ({kind})")

    if info["seq_span"]:
        lo, hi, missing = info["seq_span"]
        lines += ["", f"  SEQUENCE GAP: {missing:,} sequence number(s) absent "
                      f"between {lo} and {hi} -- ingestion loss, read from the "
                      f"file independently of the harness counters"]
    else:
        lines += ["", "  sequence: contiguous, no holes"]

    lines += ["", f"  table written to {out_csv}", "=" * 72]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("corpus", nargs="+", help="raw_*.jsonl file(s)")
    ap.add_argument("--out-dir", default=None,
                    help="where to write the CSV (default: beside the corpus)")
    ap.add_argument("--max-columns", type=int, default=64,
                    help="cap on payload columns; excess keys are reported, never dropped silently")
    args = ap.parse_args()

    rc = 0
    for raw in args.corpus:
        path = Path(raw)
        if not path.is_file():
            print(f"CONFIG_ERROR: {path} is not a file", file=sys.stderr)
            rc = 1
            continue
        info = scan(path)
        columns = [k for k, _ in info["key_counts"].most_common()]
        dropped = []
        if len(columns) > args.max_columns:
            dropped = columns[args.max_columns:]
            columns = columns[:args.max_columns]

        out_dir = Path(args.out_dir) if args.out_dir else path.parent
        out_dir.mkdir(parents=True, exist_ok=True)
        out_csv = out_dir / (path.stem + ".csv")
        rows = write_table(path, out_csv, columns)

        digest = sha256(path)
        summary = render_summary(path, info, columns, rows, out_csv, digest)
        if dropped:
            summary += (f"\n  NOTE: {len(dropped)} key(s) beyond --max-columns were "
                        f"NOT given columns: {dropped}\n")
        print(summary)
        (out_dir / (path.stem + "_table_summary.txt")).write_text(summary + "\n")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
