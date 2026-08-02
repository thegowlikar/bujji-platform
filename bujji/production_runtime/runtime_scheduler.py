"""Runtime Scheduler -- BUJJI Options OS v3, Gate F.5.

PURPOSE: decide WHICH cadenced task types are due at a given simulated
"now", from configured cadences and an injected clock only -- never a
real `sleep()`, never a background thread/timer. This is what makes
the scheduler deterministic and replayable: feed it the same sequence
of `now` timestamps twice (live or replayed) and it produces the
identical sequence of due-task decisions both times.

No trading/risk/exit logic of any kind -- this module knows nothing
about strategies, positions, or orders. It only tracks "when did each
named task last run" and compares elapsed simulated time against a
configured cadence.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Dict, List, Optional, Tuple

Clock = Callable[[], datetime]


class UnknownScheduledTaskError(Exception):
    """Raised when mark_ran()/due_tasks() references a task name that
    was never configured with a cadence."""


@dataclass(frozen=True)
class ScheduleConfig:
    """cadence_seconds=None means "run exactly once, on the first
    due_tasks() call after start" -- never fabricated as some
    arbitrary default cadence."""
    task_cadences_seconds: Dict[str, Optional[float]] = field(default_factory=dict)


class RuntimeScheduler:
    """Stateless with respect to wall-clock time -- all state is
    "last run at simulated timestamp X," advanced only by mark_ran(),
    never by this class calling the clock on its own initiative
    outside a caller-driven due_tasks()/mark_ran() call."""

    def __init__(self, config: ScheduleConfig) -> None:
        self._cadences = dict(config.task_cadences_seconds)
        self._last_ran: Dict[str, Optional[datetime]] = {name: None for name in self._cadences}

    def register_task(self, name: str, cadence_seconds: Optional[float]) -> None:
        self._cadences[name] = cadence_seconds
        self._last_ran.setdefault(name, None)

    def due_tasks(self, now: datetime) -> List[str]:
        due = []
        for name, cadence in self._cadences.items():
            last = self._last_ran.get(name)
            if last is None:
                due.append(name)
                continue
            if cadence is None:
                continue  # one-shot task, already ran once.
            elapsed = (now - last).total_seconds()
            if elapsed >= cadence:
                due.append(name)
        return sorted(due)

    def mark_ran(self, name: str, now: datetime) -> None:
        if name not in self._cadences:
            raise UnknownScheduledTaskError(f"task {name!r} was never registered with a cadence")
        self._last_ran[name] = now

    def last_ran(self, name: str) -> Optional[datetime]:
        if name not in self._cadences:
            raise UnknownScheduledTaskError(f"task {name!r} was never registered with a cadence")
        return self._last_ran[name]
