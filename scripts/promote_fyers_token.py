"""Promote freshly-refreshed FYERS tokens into the file the units actually read.

  python scripts/promote_fyers_token.py                 # promote and verify
  python scripts/promote_fyers_token.py --check         # verify only, no write
  python scripts/promote_fyers_token.py --from PATH     # non-default source

WHY THIS EXISTS. The interactive FYERS login happens off this machine, and
its output habitually lands in `/tmp/local_fyers.env` -- that path is the
argparse default of `run_phase20_13_live_entrypoint.py --fyers-env-file`,
so it is the one people remember. Nothing reads it. Every scheduled unit
reads `/opt/bujji/.env` and only that:

    bujji-daily-intelligence        EnvironmentFile=/opt/bujji/.env
    bujji-options-os-trading        EnvironmentFile=/opt/bujji/.env
    bujji-shadow-decision-campaign  ExecStart ... --fyers-env-file /opt/bujji/.env

This has already cost two trading days (2026-08-17 and 2026-08-18): a
correct, fresh token sat in /tmp while the units held an expired one.

WHY NOT A SYMLINK, AND WHY NOT A PLAIN COPY. Both were considered and both
are worse:

  * A symlink from /tmp/local_fyers.env does not survive the write. scp and
    most editors write a temp file and rename over the target, replacing
    the symlink with a regular file -- it breaks on first use, silently.
    /tmp is also `D /tmp 1777 root root 30d`, so it is cleared on boot.
  * A wholesale `cp` destroys keys. The refresh file carries 8 keys; the
    target carries 10. FYERS_PIN and FYERS_CREDENTIALS_FILE exist only in
    the target, and cp would also reset ownership to root:root, making the
    file unreadable by `bujji` -- the user every unit runs as.

So this replaces ONLY the token lines, and verifies the key set is
unchanged afterwards rather than trusting that it is.

NO SECRET IS EVER PRINTED. Values are moved between files and inspected for
expiry; only key NAMES, timestamps and expiry times reach stdout.
"""
from __future__ import annotations

import argparse
import base64
import datetime
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

TARGET = Path("/opt/bujji/.env")
DEFAULT_SOURCE = Path("/tmp/local_fyers.env")
PROMOTE = ("FYERS_ACCESS_TOKEN", "FYERS_REFRESH_TOKEN")

# Each promote writes a backup, and every backup holds LIVE CREDENTIALS.
# Left alone they accumulate one per refresh, forever -- a growing pile of
# working tokens sitting next to the real one. Five is enough to undo a bad
# promote and short enough that the pile stays reviewable.
BACKUPS_TO_KEEP = 5
_BACKUP_NAME = re.compile(r"^\.env\.bak-(\d{8}T\d{6})$")

IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
FIRST_FIRE = datetime.time(9, 10)     # bujji-shadow-decision-campaign
LAST_FIRE = datetime.time(9, 22, 30)  # bujji-options-os-trading

_KEY = re.compile(r"^\s*(?:export\s+)?([A-Z_][A-Z0-9_]*)\s*=")


def rotate_backups(directory: Path, keep: int = BACKUPS_TO_KEEP):
    """Delete all but the newest `keep` backups. Returns (kept, removed, skipped).

    DELIBERATELY PARANOID, because every one of these files contains a
    working credential and this function's only job is to delete things:

      * the name must match `.env.bak-YYYYMMDDTHHMMSS` EXACTLY -- a glob
        like `.env.bak-*` would also match `.env.bak-before-migration` or
        anything a human parked there by hand;
      * ordering comes from the timestamp IN THE NAME, not mtime, because a
        copy or a restore rewrites mtime and would silently reorder them;
      * anything that matches the shape but whose timestamp will not parse
        is SKIPPED, never deleted. An unrecognised file is a reason to stop,
        not a reason to guess.
    """
    candidates = []
    skipped = []
    for path in directory.iterdir():
        if not path.is_file():
            continue
        m = _BACKUP_NAME.match(path.name)
        if not m:
            continue
        try:
            stamp = time.strptime(m.group(1), "%Y%m%dT%H%M%S")
        except ValueError:
            skipped.append(path)
            continue
        candidates.append((stamp, path))

    candidates.sort(key=lambda pair: pair[0], reverse=True)
    kept = [path for _, path in candidates[:keep]]
    removed = []
    for _, path in candidates[keep:]:
        try:
            path.unlink()
            removed.append(path)
        except OSError as exc:      # a backup we cannot remove is reported,
            skipped.append(path)    # never silently treated as removed.
            print(f"  WARNING: could not remove {path.name}: {exc}", file=sys.stderr)
    return kept, removed, skipped


def unmanaged_backups(directory: Path):
    """Files that LOOK like token backups but rotation cannot manage.

    Rotation matches `.env.bak-YYYYMMDDTHHMMSS` exactly, which is right --
    it refuses to delete anything it cannot positively identify. The cost is
    that a look-alike is invisible: never counted, never pruned, holding a
    live credential forever.

    Found for real on 2026-08-18: `.env.bak-20260815224954` (no `T`
    separator, written by some earlier process) sat alongside three managed
    backups. Rotation reported "5 kept" while SIX credential files were on
    disk. Silence is the bug -- these are reported so a human can decide,
    and still never deleted automatically.
    """
    out = []
    for path in sorted(directory.glob(".env.bak*")):
        if path.is_file() and not _BACKUP_NAME.match(path.name):
            out.append(path)
    return out


def keyed_lines(path: Path) -> dict:
    out = {}
    for line in path.read_text().splitlines():
        m = _KEY.match(line)
        if m:
            out[m.group(1)] = line
    return out


def access_token_expiry(path: Path):
    """Decode the JWT `exp` claim. No API call, no secret printed."""
    line = keyed_lines(path).get("FYERS_ACCESS_TOKEN")
    if not line:
        return None
    token = line.split("=", 1)[1].strip().strip("\"'")
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        exp = json.loads(base64.urlsafe_b64decode(payload))["exp"]
    except Exception:
        return None
    return datetime.datetime.fromtimestamp(exp, IST)


def report(path: Path, label: str) -> None:
    exp = access_token_expiry(path)
    now = datetime.datetime.now(IST)
    if exp is None:
        print(f"  {label:<24} token unreadable or absent")
        return
    fire = datetime.datetime.combine(now.date(), LAST_FIRE, IST)
    if fire < now:
        fire += datetime.timedelta(days=1)
    state = "VALID" if exp > now else "EXPIRED"
    print(f"  {label:<24} expires {exp.isoformat()}  [{state}]")
    print(f"  {'':<24} outlives the {LAST_FIRE.strftime('%H:%M:%S')} "
          f"trading fire: {exp > fire}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from", dest="source", default=str(DEFAULT_SOURCE))
    ap.add_argument("--check", action="store_true", help="verify only, write nothing")
    ap.add_argument("--keep", type=int, default=BACKUPS_TO_KEEP,
                    help=f"backups to retain after promoting (default {BACKUPS_TO_KEEP})")
    args = ap.parse_args()
    source = Path(args.source)

    if not TARGET.exists():
        print(f"FATAL: {TARGET} does not exist -- nothing to promote into.", file=sys.stderr)
        return 2

    print("BEFORE")
    report(TARGET, "units read this:")
    if source.exists():
        report(source, "refresh file:")
    else:
        print(f"  refresh file:            {source} not found")

    if args.check:
        return 0
    if not source.exists():
        print(f"\nFATAL: {source} not found -- refresh first.", file=sys.stderr)
        return 2

    incoming = keyed_lines(source)
    missing = [k for k in PROMOTE if k not in incoming]
    if missing:
        print(f"\nFATAL: {source} is missing {missing} -- refusing to touch {TARGET}.",
              file=sys.stderr)
        return 2

    source_exp = access_token_expiry(source)
    if source_exp is None:
        print(f"\nFATAL: could not decode the token in {source} -- refusing to promote "
              "a token whose expiry cannot be checked.", file=sys.stderr)
        return 2
    if source_exp <= datetime.datetime.now(IST):
        print(f"\nFATAL: the token in {source} expired at {source_exp.isoformat()}. "
              "Refresh again -- promoting it would only replace one dead token with "
              "another.", file=sys.stderr)
        return 2

    before = set(keyed_lines(TARGET))
    stat = TARGET.stat()
    backup = TARGET.with_name(f".env.bak-{time.strftime('%Y%m%dT%H%M%S')}")
    shutil.copy2(TARGET, backup)

    lines = TARGET.read_text().splitlines(keepends=True)
    replaced = []
    for i, line in enumerate(lines):
        m = _KEY.match(line)
        if m and m.group(1) in PROMOTE:
            newline = "\n" if line.endswith("\n") else ""
            lines[i] = incoming[m.group(1)].rstrip("\n") + newline
            replaced.append(m.group(1))

    absent = [k for k in PROMOTE if k not in replaced]
    if absent:
        print(f"\nFATAL: {absent} not present in {TARGET}; nothing written. "
              f"Backup at {backup}", file=sys.stderr)
        return 2

    TARGET.write_text("".join(lines))
    os.chmod(TARGET, stat.st_mode & 0o7777)
    os.chown(TARGET, stat.st_uid, stat.st_gid)

    after = set(keyed_lines(TARGET))
    if after != before:
        print(f"\nFATAL: key set changed ({before ^ after}). Restore: cp {backup} {TARGET}",
              file=sys.stderr)
        return 2

    # Rotate only AFTER the new backup exists and the write has been
    # verified -- pruning first could leave nothing to fall back to if the
    # write below had failed.
    kept, removed, skipped = rotate_backups(TARGET.parent, args.keep)

    print(f"\nPROMOTED  {', '.join(replaced)}")
    print(f"  backup            {backup}")
    print(f"  backups kept      {len(kept)} (limit {args.keep})"
          + (f", pruned {len(removed)}" if removed else "")
          + (f", SKIPPED {len(skipped)} unrecognised" if skipped else ""))

    stray = unmanaged_backups(TARGET.parent)
    if stray:
        print(f"  UNMANAGED         {len(stray)} backup-like file(s) rotation cannot "
              "prune -- each may hold a live credential:")
        for path in stray:
            print(f"                      {path.name}")
        print("                    Review and remove by hand; the name does not match "
              "the .env.bak-YYYYMMDDTHHMMSS format.")
    print(f"  keys preserved    {len(after)}")
    print(f"  mode/owner        {oct(stat.st_mode & 0o7777)} uid={stat.st_uid} gid={stat.st_gid}")
    print("\nAFTER")
    report(TARGET, "units read this:")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
