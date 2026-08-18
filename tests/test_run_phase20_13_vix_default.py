"""Task #166 regression guard -- scripts/run_phase20_13_live_entrypoint.py's
--trailing-vix-days default must never fall below mic_v0.
volatility_classifier.MIN_WINDOW, or the classifier silently starves
itself of enough trailing history to ever produce a real percentile
(the exact bug this test pins).

A static, source-level check -- never imports or executes the script's
own async _main() (which requires live credentials, a lock file, and
either --session-date or --date-today). This mirrors the AST/source
-based safety-test convention already used elsewhere in this codebase
(e.g. tests/test_shadow_trade_construction_safety.py's git-diff check)
for exactly the same reason: verify a structural property without
running the live path.
"""
from __future__ import annotations

import re
from pathlib import Path

from bujji.mic_v0.volatility_classifier import MIN_WINDOW, classify_volatility_state

_SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "run_phase20_13_live_entrypoint.py"


def test_trailing_vix_days_default_is_at_least_min_window():
    source = _SCRIPT_PATH.read_text()
    match = re.search(r'--trailing-vix-days["\']?\s*,\s*type=int\s*,\s*default=(\d+)', source)
    assert match is not None, "could not find the --trailing-vix-days argparse default in the script source"
    default = int(match.group(1))
    assert default >= MIN_WINDOW, (
        f"--trailing-vix-days default ({default}) is below classify_volatility_state's own "
        f"MIN_WINDOW ({MIN_WINDOW}) -- the classifier would always return None (Task #166)."
    )


# Case 1: 59 days of history (one below MIN_WINDOW) -> UNKNOWN (None), never a guess.
def test_case1_below_min_window_is_unknown():
    state, evidence = classify_volatility_state(50.0, [20.0] * (MIN_WINDOW - 1))
    assert state is None
    assert any("insufficient_history" in e for e in evidence)


# Case 2: exactly MIN_WINDOW (60) days of history -> a real, valid percentile classification.
def test_case2_at_min_window_produces_real_classification():
    history = [float(v) for v in range(10, 10 + MIN_WINDOW)]
    state, evidence = classify_volatility_state(68.0, history)
    assert state is not None
    assert any("percentile=" in e for e in evidence)
