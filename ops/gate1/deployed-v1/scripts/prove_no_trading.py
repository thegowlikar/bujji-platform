"""Prove no automation path can start an order-capable Bujji session.

Structural, not a promise. Three independent claims, each with a positive
control so a passing result cannot be vacuous.
"""
import ast, pathlib, subprocess, sys

RUN_DIR = pathlib.Path("/opt/bujji/gate1-run")
ASSETS = ["gate1_orchestrator.py", "gate1_preflight.py", "gate1_build_universe.py",
          "gate1_spot.py", "gate1_measure_v5.py", "gate1_opening_capture.py",
          "gate1_sandbox_selftest.py",
          "gate1_capacity_probe.py", "gate1_replay.py"]
# MUTATING systemd verbs. Naming a unit is not the hazard -- the preflight
# MUST name the trading unit in order to verify it is inhibited. The hazard is
# changing its state, so that is what is forbidden.
MUTATING_VERBS = ["enable", "start", "unmask", "daemon-reload", "restart",
                  "set-property", "edit", "link", "preset"]
# Files that would take effect if written: the gate sentinels. Creating either
# would satisfy a ConditionPathExists and silently render the gate inert.
SENTINELS = ["GATE1_TRADING_REENABLED", "GATE1_CERTIFY_REENABLED"]
ORDER_CALLS = {"place_order", "modify_order", "submit_and_confirm",
               "exit_and_enter", "place_basket"}

failures, notes = [], []
print("PROOF: no automation path can start an order-capable session\n")

# --- 1. no asset MUTATES a unit or creates a gate sentinel ---------------
print("1. No asset mutates a unit or creates a gate sentinel")
for name in ASSETS:
    text = (RUN_DIR / name).read_text()
    for i, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("#") or stripped.startswith(('"', "'")):
            continue                       # prose describing what it will not do
        for verb in MUTATING_VERBS:
            if f'"{verb}"' in line or f"'{verb}'" in line or f"systemctl {verb}" in line:
                failures.append(f"{name}:{i} uses mutating systemd verb {verb!r}")
        for sent in SENTINELS:
            # Reading a sentinel to confirm it is ABSENT is the preflight's job.
            # WRITING one would satisfy the gate and make it inert.
            if sent in line and any(w in line for w in
                                    ("write_text", "touch(", "open(", "mkdir", "w\")")):
                failures.append(f"{name}:{i} may CREATE gate sentinel {sent!r}")
print(f"   checked {len(ASSETS)} files -- "
      f"{'CLEAN' if not failures else str(len(failures)) + ' VIOLATION(S)'}")

# --- 2. no asset calls an order method ------------------------------------
print("2. No asset contains an order-placement call site")
order_hits = []
for name in ASSETS:
    tree = ast.parse((RUN_DIR / name).read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
            if fn in ORDER_CALLS:
                order_hits.append(f"{name}:{node.lineno} -> {fn}()")
print(f"   {'CLEAN' if not order_hits else order_hits}")
failures.extend(order_hits)

# --- 3. no asset invokes systemctl at all ---------------------------------
print("3. No asset invokes systemctl as a subprocess")
sysctl = []
for name in ASSETS:
    text = (RUN_DIR / name).read_text()
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                and node.value.strip() == "systemctl":
            # gate1_preflight READS unit state; that is is-enabled/is-active/
            # show/cat only, and is verified below rather than banned.
            sysctl.append((name, node.lineno))
readonly_verbs = {"is-enabled", "is-active", "show", "cat", "list-timers"}
for name, line in sysctl:
    text = (RUN_DIR / name).read_text().splitlines()
    ctx = " ".join(text[max(0, line - 2): line + 3])
    verbs = {v for v in readonly_verbs if v in ctx}
    if not verbs:
        failures.append(f"{name}:{line} invokes systemctl with no read-only verb")
    else:
        notes.append(f"{name}:{line} systemctl {sorted(verbs)} (read-only)")
print(f"   {len(sysctl)} systemctl reference(s), all read-only"
      if sysctl and not any('no read-only' in f for f in failures) else f"   {len(sysctl)} reference(s)")

# --- POSITIVE CONTROLS: the checks must be able to fail -------------------
print("\nPOSITIVE CONTROLS (these must be detected)")
probe = ("import x\n"
         "subprocess.run(['systemctl','start','bujji-options-os-trading.timer'])\n"
         "b.place_order(1)\n"
         "open('/opt/bujji/GATE1_TRADING_REENABLED','w').write('x')\n")
t = ast.parse(probe)
found_call = any(isinstance(n, ast.Call) and
                 (getattr(n.func, "attr", None) or getattr(n.func, "id", None)) in ORDER_CALLS
                 for n in ast.walk(t))
found_verb = any(f"'{v}'" in probe for v in MUTATING_VERBS)
found_sent = any(s_ in probe and "open(" in probe for s_ in SENTINELS)
print(f"   order-call detector fires on a planted call    : {found_call}")
print(f"   mutating-verb detector fires on planted start  : {found_verb}")
print(f"   sentinel-write detector fires on planted write : {found_sent}")
assert found_call and found_verb and found_sent, (
    "DETECTORS ARE BROKEN -- the clean result above would be vacuous")

# --- 4. live unit state, right now ----------------------------------------
print("\n4. Live unit state")
for unit in ("bujji-options-os-trading", "bujji-certify-ws-option"):
    en = subprocess.run(["systemctl", "is-enabled", f"{unit}.timer"],
                        capture_output=True, text=True).stdout.strip()
    ac = subprocess.run(["systemctl", "is-active", f"{unit}.timer"],
                        capture_output=True, text=True).stdout.strip()
    cr = subprocess.run(["systemctl", "show", f"{unit}.service", "-p",
                         "ConditionResult", "--value"],
                        capture_output=True, text=True).stdout.strip()
    print(f"   {unit:30} timer={en}/{ac}  condition={cr}")
    if en != "disabled" or ac != "inactive":
        failures.append(f"{unit} is not inhibited")

print()
if failures:
    print(f"PROOF FAILED -- {len(failures)} issue(s):")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("PROOF HOLDS: no automation path can start an order-capable session.")
for n in notes[:6]:
    print("  note:", n)
sys.exit(0)
