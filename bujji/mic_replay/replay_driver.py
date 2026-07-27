"""MIC Replay Driver — BUJJI Options OS v3, Engineering Series 61.

Feeds historical observations into the frozen MIC v2 engine and
captures every Evidence artifact it emits -- without ever importing
MIC v2 into this process. MIC v2 lives in a completely separate
Python environment (`/opt/bujji-mic-v2/`, its own venv, its own repo
-- Series 54's own disclosed process-boundary finding, unchanged and
respected here). This driver invokes MIC v2's own, already-existing,
frozen `mic_v2.runner.replay_runner.run_replay()` via a subprocess
running MIC v2's own interpreter -- it never reimplements, shortcuts,
or bypasses any MIC v2 logic, and no line of MIC v2 source is
modified.

Determinism: `run_replay()` (frozen, MIC v2 Sprint code) is itself
documented as deterministic -- every Evidence field is computed from
`ObservationInput` fields only, never wall-clock, never randomness.
This driver adds nothing non-deterministic of its own: the subprocess
call's only inputs are the JSON-encoded candle/option-chain payloads
built by `observation_adapter.py`.

Disclosed scope boundary: this driver reconstructs MIC v2's own
Evidence-level replay output (the `mic_v2.engine.run_cycle` /
`runner.replay_runner.run_replay` layer). It does NOT reconstruct the
further downstream classification-string layers (Context, Opinion,
Context Stability, Calibration, Governance, Lifecycle, Contract) that
Series 32's Evidence Interpreter ultimately consumes as
`market_context`/`market_opinion`/etc. -- those live in separate MIC v2
subpackages (`context/`, `opinion/`, `context_stability/`,
`calibration/`, `governance/`, `lifecycle/`, `contract/`) whose own
publication APIs were not mapped and verified within this sprint's
scope. See `docs/HISTORICAL_MIC_REPLAY.md` for the full disclosure and
what remains to complete that mapping.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

DEFAULT_MIC_V2_PYTHON = "/opt/bujji-mic-v2/.venv/bin/python"
DEFAULT_MIC_V2_CWD = "/opt/bujji-mic-v2"

# This is the ONLY code that ever runs inside MIC v2's own process. It
# imports exclusively from `mic_v2.*` (never `bujji.*`, preserving the
# same repo isolation MIC v2 already enforces on itself), reads a JSON
# payload from stdin, calls MIC v2's own frozen `run_replay()`
# unmodified, and writes a JSON result to stdout. It is never written
# to disk inside the MIC v2 repo -- it is passed as a `-c` argument, so
# MIC v2's own repository is never touched by this sprint.
_BRIDGE_SCRIPT = r"""
import json, sys
from datetime import datetime
from mic_v2.models.evidence import Candle, OptionChainLevel
from mic_v2.runner.replay_runner import run_replay

payload = json.load(sys.stdin)
candles = []
chain_by_ts = {}
for obs in payload["observations"]:
    c = obs["candle"]
    candles.append(Candle(
        timestamp=datetime.fromisoformat(c["timestamp"]),
        open=c["open"], high=c["high"], low=c["low"], close=c["close"],
        volume=c.get("volume", 0.0),
    ))
    chain_by_ts[c["timestamp"]] = obs.get("option_chain", [])

evidences = run_replay(candles)

out = []
for e in evidences:
    out.append({
        "timestamp": e.timestamp.isoformat(),
        "module": e.module,
        "signal": e.signal,
        "confidence": e.confidence,
        "supporting_evidence": e.supporting_evidence,
    })
json.dump({"evidence": out, "candle_count": len(candles)}, sys.stdout)
"""


@dataclass(frozen=True)
class MicReplayResult:
    evidence: tuple
    candle_count: int
    stderr: str
    returncode: int


class MicReplayError(Exception):
    """Raised when the MIC v2 subprocess fails or returns malformed
    output -- never silently swallowed, and this driver never
    fabricates a substitute Evidence list on failure.
    """


def run_mic_replay(
    observations: Sequence[Dict],
    mic_v2_python: str = DEFAULT_MIC_V2_PYTHON,
    mic_v2_cwd: str = DEFAULT_MIC_V2_CWD,
    timeout_seconds: float = 60.0,
) -> MicReplayResult:
    """Feed a sequence of `observation_adapter.build_observation_payload()`
    outputs into MIC v2's own frozen `run_replay()`, via subprocess, in
    original chronological order. Raises `MicReplayError` on any
    subprocess failure or malformed output -- never returns a
    fabricated result.
    """
    if not observations:
        return MicReplayResult(evidence=(), candle_count=0, stderr="", returncode=0)

    payload = json.dumps({"observations": list(observations)})

    try:
        proc = subprocess.run(
            [mic_v2_python, "-c", _BRIDGE_SCRIPT],
            input=payload,
            capture_output=True,
            text=True,
            cwd=mic_v2_cwd,
            timeout=timeout_seconds,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise MicReplayError(f"Failed to invoke MIC v2 replay subprocess: {exc!r}") from exc

    if proc.returncode != 0:
        raise MicReplayError(f"MIC v2 replay subprocess exited {proc.returncode}: {proc.stderr}")

    try:
        result = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise MicReplayError(f"MIC v2 replay subprocess produced non-JSON output: {exc!r}") from exc

    return MicReplayResult(
        evidence=tuple(result["evidence"]),
        candle_count=result["candle_count"],
        stderr=proc.stderr,
        returncode=proc.returncode,
    )
