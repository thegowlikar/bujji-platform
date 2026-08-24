"""Positive-controlled. An absence claim is worthless if the closure is empty."""
import ast, pathlib

ROOT = pathlib.Path("/opt/bujji/app")
ENTRYPOINTS = {
    "bujji-options-os-trading": ROOT / "bujji_options_os_runner.py",
    "bujji-shadow-decision-campaign": ROOT / "scripts/run_phase20_13_live_entrypoint.py",
    "bujji-daily-intelligence": ROOT / "run_daily_intelligence_session.py",
    "bujji-futures-depth-poller": ROOT / "scripts/run_futures_depth_poller.py",
    "bujji-certify-ws-option": pathlib.Path("/opt/bujji/certify_ws_option.py"),
    "bujji-token-preflight": ROOT / "scripts/preflight_fyers_token.py",
    "bujji-price-levels": ROOT / "scripts/refresh_price_levels.py",
    "bujji-backup": ROOT / "scripts/backup_observation_stores.py",
}
PLACEMENT_CALLS = {"place_order", "submit_and_confirm", "exit_and_enter",
                   "modify_order", "place_basket"}
DEFINITION_ONLY = {"bujji/broker/base.py", "bujji/broker/errors.py",
                   "bujji/core/models.py", "bujji/core/enums.py"}


def module_path(dotted):
    for cand in (ROOT / (dotted.replace(".", "/") + ".py"),
                 ROOT / (dotted.replace(".", "/") + "/__init__.py")):
        if cand.exists():
            return cand
    return None


def imports_of(path):
    try:
        tree = ast.parse(path.read_text())
    except Exception:
        return set()
    out = set()
    try:
        pkg_parts = path.relative_to(ROOT).parent.as_posix().split("/")
    except ValueError:
        pkg_parts = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                out.add(a.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level and pkg_parts and pkg_parts != [""]:
                base = ".".join(pkg_parts[:len(pkg_parts) - node.level + 1])
                target = f"{base}.{node.module}" if node.module else base
            elif node.module:
                target = node.module
            else:
                continue
            out.add(target)
            for a in node.names:
                out.add(f"{target}.{a.name}")
    return out


def closure(start):
    files, mods, queue = {start}, set(), [start]
    while queue:
        for dotted in imports_of(queue.pop()):
            if not dotted.startswith("bujji") or dotted in mods:
                continue
            mods.add(dotted)
            p = module_path(dotted)
            if p and p not in files:
                files.add(p)
                queue.append(p)
    return files


def call_sites(path):
    try:
        rel = str(path.relative_to(ROOT))
    except ValueError:
        rel = str(path)
    if rel in DEFINITION_ONLY:
        return []
    try:
        tree = ast.parse(path.read_text())
    except Exception:
        return []
    return [f"{rel}:{n.lineno} -> {getattr(n.func,'attr',None) or getattr(n.func,'id',None)}()"
            for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and (getattr(n.func, "attr", None) or getattr(n.func, "id", None)) in PLACEMENT_CALLS]


# --- POSITIVE CONTROL: the analyser must find the known-capable unit -------
control = [h for f in closure(ENTRYPOINTS["bujji-options-os-trading"]) for h in call_sites(f)]
print(f"POSITIVE CONTROL -- known entry-capable unit yields {len(control)} call site(s)")
assert control, "ANALYSER IS BROKEN: it cannot even find the trading runner's placements"
probe = ROOT / "bujji/broker/paper.py"
print(f"POSITIVE CONTROL -- paper.py itself yields "
      f"{len([n for n in ast.walk(ast.parse(probe.read_text())) if isinstance(n, ast.Call)])} calls "
      f"(parser works on broker files)")
print()

print(f"{'UNIT':34} {'CLOSURE':>8}  {'VERDICT':22} SITES")
print("-" * 100)
summary = {}
for unit, start in sorted(ENTRYPOINTS.items()):
    if not start.exists():
        print(f"{unit:34} {'--':>8}  {'ENTRYPOINT MISSING':22} {start}")
        continue
    files = closure(start)
    hits = [h for f in sorted(files) for h in call_sites(f)]
    summary[unit] = (len(files), hits)
    verdict = "ENTRY-CAPABLE" if hits else "no placement call site"
    flag = "" if len(files) > 3 else "  <-- CLOSURE TOO SMALL TO TRUST"
    print(f"{unit:34} {len(files):>8}  {verdict:22} {len(hits)}{flag}")
    for h in hits[:5]:
        print(f"{'':44}   {h}")

print()
print("MUST BE INHIBITED:", sorted(u for u, (_, h) in summary.items() if h))
suspect = sorted(u for u, (n, h) in summary.items() if not h and n <= 3)
if suspect:
    print("ABSENCE NOT TRUSTWORTHY (tiny closure -- verify by hand):", suspect)
