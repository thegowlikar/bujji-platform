"""Does the opening buffer actually guarantee containment across a gap?

Measured, not asserted. Builds a provisional set at a prior reference price
and canonical sets at a range of simulated opens, then checks whether every
canonical symbol was already in the provisional subscription set.
"""
import subprocess, json, sys, pathlib

PRIOR = 24252.0
BUFFER = 500
OUT = "/tmp/ct"

def build(spot, provisional, session):
    cmd = ["/opt/bujji/.venv/bin/python", "gate1_build_universe.py",
           "--spot", str(spot), "--spot-source", "containment test",
           "--session-id", session, "--out", OUT]
    if provisional:
        cmd += ["--provisional", "--opening-buffer-points", str(BUFFER)]
    r = subprocess.run(cmd, capture_output=True, text=True, cwd="/opt/bujji/gate1-run")
    if r.returncode != 0:
        return None
    stem = "provisional_universe" if provisional else "universe"
    doc = json.loads(pathlib.Path(f"{OUT}/{session}/{stem}.json").read_text())
    return {i["symbol"] for i in doc["instruments"]}

prov = build(PRIOR, True, "PROV")
print(f"provisional at prior {PRIOR:.0f} (buffer {BUFFER}): {len(prov)} symbols\n")
print(f"{'open':>9} {'gap':>8} {'gap %':>7} {'canonical':>10} {'missing':>8}  containment")
print("-" * 62)

worst_ok = 0
for gap in (0, 100, 250, 400, 500, 600, 800):
    for sign in (1, -1):
        if gap == 0 and sign == -1:
            continue
        opened = PRIOR + sign * gap
        canon = build(opened, False, f"CANON_{sign}_{gap}")
        if canon is None:
            print(f"{opened:>9.0f} {sign*gap:>+8} {'--':>7} {'REFUSED':>10}")
            continue
        missing = canon - prov
        ok = not missing
        if ok:
            worst_ok = max(worst_ok, gap)
        print(f"{opened:>9.0f} {sign*gap:>+8} {100*gap/PRIOR:>6.2f}% {len(canon):>10} "
              f"{len(missing):>8}  {'HELD' if ok else 'BROKEN'}")

print()
print(f"Containment holds out to a gap of at least {worst_ok} points "
      f"({100*worst_ok/PRIOR:.2f}%).")
print("NIFTY overnight gaps beyond 2% are rare but not impossible; a gap past "
      "the buffer\nis DETECTED and graded FAIL, never hidden.")
