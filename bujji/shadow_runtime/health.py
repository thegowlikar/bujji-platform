"""Live Health Heartbeat -- Shadow Runtime, Phase 19.10.1.

Closes Phase 19.10.0's own confirmed gap: `runtime_health` previously
existed only inside the final `ShadowSessionArtifact`, available only
AFTER a session finished. This module writes a small JSON file that is
OVERWRITTEN (never appended) on every real stage transition, so an
external process can ask "is this alive, and what is it doing right
now" while a session is still running.

Pure file IO, no broker, no network. `at`/`session_date` are always
caller-supplied (from the same injected clock the rest of the runtime
already uses) -- no wall-clock call anywhere in this module.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Dict, Optional

from .lifecycle import RuntimeStage

STATUS_RUNNING = "RUNNING"
STATUS_COMPLETED = "COMPLETED"
STATUS_FAILED = "FAILED"
ALL_STATUSES = (STATUS_RUNNING, STATUS_COMPLETED, STATUS_FAILED)

_STAGE_TO_STATUS = {
    RuntimeStage.COMPLETED: STATUS_COMPLETED,
    RuntimeStage.FAILED: STATUS_FAILED,
}


@dataclass(frozen=True)
class RuntimeHealth:
    runtime: str
    status: str
    session_date: str
    current_stage: str
    last_heartbeat: str
    last_error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "runtime": self.runtime, "status": self.status, "session_date": self.session_date,
            "current_stage": self.current_stage, "last_heartbeat": self.last_heartbeat,
            "last_error": self.last_error,
        }


def health_for_stage(
    stage: RuntimeStage, *, session_date: str, at: str, last_error: Optional[str] = None,
    runtime: str = "shadow_runtime",
) -> RuntimeHealth:
    status = _STAGE_TO_STATUS.get(stage, STATUS_RUNNING)
    return RuntimeHealth(
        runtime=runtime, status=status, session_date=session_date,
        current_stage=stage.value, last_heartbeat=at, last_error=last_error,
    )


def write_health_heartbeat(path: str, health: RuntimeHealth) -> None:
    """Overwrites `path` atomically (write to a temp file, then rename)
    so a reader never sees a torn/partial write mid-update -- the same
    "never a corrupted line" discipline `EventStore` already applies to
    its own appends, adapted here for a single-record file that is
    replaced wholesale each time rather than appended to."""
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w") as f:
        json.dump(health.to_dict(), f)
    os.replace(tmp_path, path)


def read_health_heartbeat(path: str) -> Optional[RuntimeHealth]:
    if not os.path.exists(path):
        return None
    with open(path) as f:
        d = json.load(f)
    return RuntimeHealth(
        runtime=d["runtime"], status=d["status"], session_date=d["session_date"],
        current_stage=d["current_stage"], last_heartbeat=d["last_heartbeat"], last_error=d.get("last_error"),
    )
