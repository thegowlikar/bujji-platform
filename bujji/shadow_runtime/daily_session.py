"""Daily Session Runtime -- Shadow Runtime, Phase 19.11.

The single authoritative daily runtime entrypoint: starts observation
capture, starts the shadow intelligence runtime (Phase 19.10.2's
already-wired loop), monitors health, finalizes the session. This
module is reliability engineering only -- no intelligence logic, no
strategy, no execution, no broker order/position code anywhere.

`capture_fn`/`intelligence_fn` are injected (same convention as
`ShadowSessionRunner`'s own `clock`/`sleep_fn`/`broker`) so this module
never itself decides HOW capture or intelligence runs -- it only
orchestrates calling them, in order, and records what happened. This
is exactly the reuse this phase requires: `capture_fn` in production
wraps the already-existing `capture_market_reality_session.py`/
`capture_options_reality_session.py` scripts (subprocess or direct
call, a real Phase 19.11.1-scale wiring decision this module does not
make), and `intelligence_fn` in production wraps the already-existing,
already-wired `ShadowSessionRunner.start()` (Phase 19.10.2). Neither is
reimplemented here.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Awaitable, Callable, Dict, Optional, Tuple


class DailySessionStage(str, Enum):
    PRE_MARKET = "PRE_MARKET"
    MARKET_OPEN = "MARKET_OPEN"
    CAPTURING = "CAPTURING"
    INTELLIGENCE_RUNNING = "INTELLIGENCE_RUNNING"
    SESSION_COMPLETE = "SESSION_COMPLETE"
    FAILED = "FAILED"


# Same documented, exhaustive, fail-closed transition-table discipline
# `shadow_runtime.lifecycle.RuntimeStage` already established (Phase
# 19.10.1) -- a distinct state vocabulary (session-level, not
# cycle-level), so a separate table, not a shared one; the DESIGN
# PATTERN is reused, not the specific enum.
_LEGAL_TRANSITIONS: Dict[DailySessionStage, Tuple[DailySessionStage, ...]] = {
    DailySessionStage.PRE_MARKET: (DailySessionStage.MARKET_OPEN, DailySessionStage.FAILED),
    DailySessionStage.MARKET_OPEN: (DailySessionStage.CAPTURING, DailySessionStage.FAILED),
    DailySessionStage.CAPTURING: (DailySessionStage.INTELLIGENCE_RUNNING, DailySessionStage.SESSION_COMPLETE, DailySessionStage.FAILED),
    DailySessionStage.INTELLIGENCE_RUNNING: (DailySessionStage.SESSION_COMPLETE, DailySessionStage.FAILED),
    DailySessionStage.SESSION_COMPLETE: (),
    DailySessionStage.FAILED: (),
}
TERMINAL_STAGES = (DailySessionStage.SESSION_COMPLETE, DailySessionStage.FAILED)


class IllegalDailySessionTransition(RuntimeError):
    pass


@dataclass(frozen=True)
class DailySessionTransition:
    from_stage: Optional[DailySessionStage]
    to_stage: DailySessionStage
    at: str
    reason: str = ""

    def to_dict(self) -> dict:
        return {"from_stage": self.from_stage.value if self.from_stage else None,
                "to_stage": self.to_stage.value, "at": self.at, "reason": self.reason}


@dataclass(frozen=True)
class DailySessionLifecycle:
    current_stage: DailySessionStage
    transitions: Tuple[DailySessionTransition, ...] = field(default_factory=tuple)

    @staticmethod
    def start(at: str) -> "DailySessionLifecycle":
        t = DailySessionTransition(from_stage=None, to_stage=DailySessionStage.PRE_MARKET, at=at)
        return DailySessionLifecycle(current_stage=DailySessionStage.PRE_MARKET, transitions=(t,))

    def advance(self, to_stage: DailySessionStage, *, at: str, reason: str = "") -> "DailySessionLifecycle":
        legal = _LEGAL_TRANSITIONS[self.current_stage]
        if to_stage not in legal:
            raise IllegalDailySessionTransition(
                f"{self.current_stage.value} -> {to_stage.value} is not legal "
                f"(legal targets: {[s.value for s in legal]})"
            )
        t = DailySessionTransition(from_stage=self.current_stage, to_stage=to_stage, at=at, reason=reason)
        return DailySessionLifecycle(current_stage=to_stage, transitions=self.transitions + (t,))

    def is_terminal(self) -> bool:
        return self.current_stage in TERMINAL_STAGES


@dataclass(frozen=True)
class DailySessionHeartbeat:
    """The 5 Phase 19.11 required fields, plus 3 Phase 19.14.1 additive
    fields (all defaulted -- every existing construction/read site keeps
    working unchanged). Written atomically (same write-then-`os.replace`
    technique `shadow_runtime.health` already uses, Phase 19.10.1 --
    reused here, not reinvented)."""

    session_date: str
    runtime_status: str
    last_observation_timestamp: Optional[str]
    last_intelligence_cycle_timestamp: Optional[str]
    rows_captured_today: int
    last_error: Optional[str] = None
    # Phase 19.14.1 -- EOD Completeness + LIVE/REPLAY Equivalence,
    # additive. `completeness_status` mirrors
    # `market_reality_snapshot.models.ALL_COMPLETENESS_STATES`, plus the
    # sentinel "NOT_RUN" when no `completeness_fn` was ever supplied
    # (e.g. `--dry-run`) -- never conflated with a real COMPLETE/PARTIAL/
    # EMPTY finding.
    completeness_status: Optional[str] = None
    missing_reality_components: Tuple[str, ...] = ()
    replay_equivalent: Optional[bool] = None

    def to_dict(self) -> dict:
        return {
            "session_date": self.session_date, "runtime_status": self.runtime_status,
            "last_observation_timestamp": self.last_observation_timestamp,
            "last_intelligence_cycle_timestamp": self.last_intelligence_cycle_timestamp,
            "rows_captured_today": self.rows_captured_today, "last_error": self.last_error,
            "completeness_status": self.completeness_status,
            "missing_reality_components": list(self.missing_reality_components),
            "replay_equivalent": self.replay_equivalent,
        }

    @staticmethod
    def from_dict(d: dict) -> "DailySessionHeartbeat":
        return DailySessionHeartbeat(
            session_date=d["session_date"], runtime_status=d["runtime_status"],
            last_observation_timestamp=d.get("last_observation_timestamp"),
            last_intelligence_cycle_timestamp=d.get("last_intelligence_cycle_timestamp"),
            rows_captured_today=d.get("rows_captured_today", 0), last_error=d.get("last_error"),
            completeness_status=d.get("completeness_status"),
            missing_reality_components=tuple(d.get("missing_reality_components", ())),
            replay_equivalent=d.get("replay_equivalent"),
        )


def write_daily_heartbeat(path: str, heartbeat: DailySessionHeartbeat) -> None:
    import json
    import os
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w") as f:
        json.dump(heartbeat.to_dict(), f)
    os.replace(tmp_path, path)


def read_daily_heartbeat(path: str) -> Optional[DailySessionHeartbeat]:
    import json
    import os
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return DailySessionHeartbeat.from_dict(json.load(f))


@dataclass(frozen=True)
class CaptureResult:
    """The real, honest result of one capture invocation -- never
    assumed successful. `rows_captured` is the real count the injected
    `capture_fn` reports, never estimated."""

    rows_captured: int
    last_observation_timestamp: Optional[str]
    errors: Tuple[str, ...] = ()


@dataclass(frozen=True)
class IntelligenceRunResult:
    """Phase 19.14.1 adds 3 additive replay-equivalence fields (all
    defaulted -- every existing 3-field construction site, including
    Phase 19.11's own tests, keeps working unchanged). The LIVE/REPLAY
    equivalence check itself is NOT computed here -- it runs inside
    `live_intelligence_cycle.run_live_intelligence_cycle()` (Phase
    19.13's own canonical `replay_equivalence.validate_live_replay_equivalence`,
    reused unmodified), against the SAME reality_snapshot/candles the
    live cycle itself just used. This dataclass only carries that
    already-computed result back to `DailySessionRuntime`, which has no
    reality/intelligence-shaped objects of its own to compute it from."""

    cycles_completed: int
    last_intelligence_cycle_timestamp: Optional[str]
    errors: Tuple[str, ...] = ()
    replay_equivalent: Optional[bool] = None
    replay_mismatches: Tuple[str, ...] = ()
    replay_check_error: Optional[str] = None


@dataclass(frozen=True)
class CompletenessCheckResult:
    """Phase 19.14.1. The already-computed result of calling the
    canonical `bujji.shadow_runtime.completeness.validate_end_of_day_completeness()`
    (Phase 19.11, unmodified) -- this dataclass only carries that result
    into `DailySessionReport`/`DailySessionHeartbeat`; it computes
    nothing itself. `ran=False` means no `completeness_fn` was supplied
    (e.g. `--dry-run`) or the check itself raised -- never confused with
    a real `is_complete=False` finding, which requires `ran=True`."""

    ran: bool
    is_complete: bool
    completeness_status: str  # ALL_COMPLETENESS_STATES value, or "NOT_RUN".
    missing_components: Tuple[str, ...]
    as_of: str
    errors: Tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "ran": self.ran, "is_complete": self.is_complete, "completeness_status": self.completeness_status,
            "missing_components": list(self.missing_components), "as_of": self.as_of, "errors": list(self.errors),
        }


CompletenessFn = Callable[[], Awaitable[CompletenessCheckResult]]


@dataclass(frozen=True)
class DailySessionReport:
    """The final, real outcome of one daily session -- returned from
    `DailySessionRuntime.run()`, never raised as an exception (same
    "never raises, always finalizes" discipline `ShadowSessionRunner.start()`
    already established, Phase-5 onward)."""

    session_date: str
    final_stage: str
    capture_result: Optional[CaptureResult]
    intelligence_result: Optional[IntelligenceRunResult]
    errors: Tuple[str, ...]
    lifecycle: DailySessionLifecycle
    completeness_result: Optional[CompletenessCheckResult] = None  # Phase 19.14.1, additive.

    def to_dict(self) -> dict:
        return {
            "session_date": self.session_date, "final_stage": self.final_stage,
            "capture_result": {
                "rows_captured": self.capture_result.rows_captured,
                "last_observation_timestamp": self.capture_result.last_observation_timestamp,
                "errors": list(self.capture_result.errors),
            } if self.capture_result else None,
            "intelligence_result": {
                "cycles_completed": self.intelligence_result.cycles_completed,
                "last_intelligence_cycle_timestamp": self.intelligence_result.last_intelligence_cycle_timestamp,
                "errors": list(self.intelligence_result.errors),
                "replay_equivalent": self.intelligence_result.replay_equivalent,
                "replay_mismatches": list(self.intelligence_result.replay_mismatches),
                "replay_check_error": self.intelligence_result.replay_check_error,
            } if self.intelligence_result else None,
            "completeness_result": self.completeness_result.to_dict() if self.completeness_result else None,
            "errors": list(self.errors),
        }


CaptureFn = Callable[[], Awaitable[CaptureResult]]
IntelligenceFn = Callable[[], Awaitable[IntelligenceRunResult]]
Clock = Callable[[], datetime]


class DailySessionRuntime:
    """The orchestrator. Never captures data itself, never runs a brain
    itself -- calls the injected `capture_fn`/`intelligence_fn`, which
    production wiring points at the already-existing, unmodified capture
    scripts and `ShadowSessionRunner.start()` respectively."""

    def __init__(
        self, *, session_date: str, clock: Clock, capture_fn: CaptureFn, intelligence_fn: IntelligenceFn,
        heartbeat_path: Optional[str] = None, completeness_fn: Optional[CompletenessFn] = None,
    ) -> None:
        self._session_date = session_date
        self._clock = clock
        self._capture_fn = capture_fn
        self._intelligence_fn = intelligence_fn
        self._heartbeat_path = heartbeat_path
        # Phase 19.14.1 -- EOD Completeness. Off by default
        # (`completeness_fn=None`): every pre-existing caller/test keeps
        # behaving byte-for-byte identically, since the completeness
        # step is skipped entirely (never even a "NOT_RUN" heartbeat
        # write difference beyond the new, additively-defaulted fields
        # themselves) unless a caller opts in.
        self._completeness_fn = completeness_fn
        self._lifecycle: Optional[DailySessionLifecycle] = None
        self._rows_captured_today = 0
        self._last_observation_timestamp: Optional[str] = None
        self._last_intelligence_cycle_timestamp: Optional[str] = None
        self._completeness_status: Optional[str] = None
        self._missing_reality_components: Tuple[str, ...] = ()
        self._replay_equivalent: Optional[bool] = None

    def _advance(self, to_stage: DailySessionStage, *, reason: str = "") -> None:
        at = self._clock().isoformat()
        if self._lifecycle is None:
            self._lifecycle = DailySessionLifecycle.start(at=at)
        else:
            self._lifecycle = self._lifecycle.advance(to_stage, at=at, reason=reason)
        self._write_heartbeat(last_error=reason or None)

    def _write_heartbeat(self, *, last_error: Optional[str] = None) -> None:
        if self._heartbeat_path is None or self._lifecycle is None:
            return
        heartbeat = DailySessionHeartbeat(
            session_date=self._session_date, runtime_status=self._lifecycle.current_stage.value,
            last_observation_timestamp=self._last_observation_timestamp,
            last_intelligence_cycle_timestamp=self._last_intelligence_cycle_timestamp,
            rows_captured_today=self._rows_captured_today, last_error=last_error,
            completeness_status=self._completeness_status,
            missing_reality_components=self._missing_reality_components,
            replay_equivalent=self._replay_equivalent,
        )
        write_daily_heartbeat(self._heartbeat_path, heartbeat)

    async def run(self) -> DailySessionReport:
        """Never raises -- every real failure is caught, recorded, and
        the session transitions to FAILED with a real reason, exactly
        matching `ShadowSessionRunner.start()`'s own established
        discipline."""
        errors: list = []
        capture_result: Optional[CaptureResult] = None
        intelligence_result: Optional[IntelligenceRunResult] = None
        completeness_result: Optional[CompletenessCheckResult] = None

        self._advance(DailySessionStage.PRE_MARKET)
        try:
            self._advance(DailySessionStage.MARKET_OPEN)

            self._advance(DailySessionStage.CAPTURING)
            capture_result = await self._capture_fn()
            self._rows_captured_today = capture_result.rows_captured
            self._last_observation_timestamp = capture_result.last_observation_timestamp
            self._write_heartbeat()
            if capture_result.errors:
                errors.extend(f"capture_error: {e}" for e in capture_result.errors)
            # Capture is never fatal to the remaining steps -- an
            # incomplete capture must still let intelligence/completeness
            # run and report honestly, never silently short-circuit
            # (Phase 19.14.1's own "if capture is incomplete: still
            # preserve available artifacts" requirement).

            self._advance(DailySessionStage.INTELLIGENCE_RUNNING)
            intelligence_result = await self._intelligence_fn()
            self._last_intelligence_cycle_timestamp = intelligence_result.last_intelligence_cycle_timestamp
            self._replay_equivalent = intelligence_result.replay_equivalent
            self._write_heartbeat()
            if intelligence_result.errors:
                errors.extend(f"intelligence_error: {e}" for e in intelligence_result.errors)
            # Phase 19.13's `run_live_intelligence_cycle()` already runs
            # the HISTORICAL REPLAY composition and the LIVE/REPLAY
            # equivalence comparison internally (against the SAME
            # reality_snapshot/candles the live cycle itself used,
            # reusing `replay_equivalence.validate_live_replay_equivalence`
            # unmodified) and returns the result on `intelligence_result`
            # -- never a second, competing equivalence mechanism here.
            # A replay CHECK failure (the comparison itself couldn't
            # run) is recorded distinctly from a replay MISMATCH (the
            # comparison ran and disagreed) -- neither is ever silently
            # treated as `replay_equivalent=True`.
            if intelligence_result.replay_check_error:
                errors.append(f"replay_check_failed: {intelligence_result.replay_check_error}")
            elif intelligence_result.replay_equivalent is False:
                errors.append(f"replay_equivalence_mismatch: {list(intelligence_result.replay_mismatches)}")

            # Phase 19.14.1 -- EOD Completeness, wired in after
            # intelligence (per this phase's own required ordering).
            # Reuses `bujji.shadow_runtime.completeness.validate_end_of_day_completeness()`
            # (Phase 19.11, unmodified) via the injected `completeness_fn`
            # -- never reimplemented here. Runs even if capture/
            # intelligence reported errors above, since EOD completeness
            # reflects the real, independent state of
            # `HistoricalObservationStore` for the day, not whether
            # today's live cycle itself succeeded.
            if self._completeness_fn is not None:
                try:
                    completeness_result = await self._completeness_fn()
                except Exception as exc:  # noqa: BLE001 -- additive step, must never break the main sequence.
                    completeness_result = CompletenessCheckResult(
                        ran=False, is_complete=False, completeness_status="NOT_RUN",
                        missing_components=(), as_of="",
                        errors=(f"{type(exc).__name__}: {exc}",),
                    )
                self._completeness_status = completeness_result.completeness_status
                self._missing_reality_components = completeness_result.missing_components
                self._write_heartbeat()
                if completeness_result.errors:
                    errors.extend(f"eod_completeness_check_error: {e}" for e in completeness_result.errors)
                elif not completeness_result.is_complete:
                    # A real, disclosed finding -- never fabricated
                    # confidence over an incomplete day. Distinct prefix
                    # from `eod_completeness_check_error` so a reader can
                    # tell "the check itself broke" apart from "the check
                    # ran and today's reality is genuinely incomplete."
                    errors.append(
                        f"eod_completeness_incomplete: status={completeness_result.completeness_status} "
                        f"missing={list(completeness_result.missing_components)}"
                    )
        except asyncio.CancelledError:
            # Phase 19.12 -- Graceful Shutdown. A SIGTERM/SIGINT (or any
            # other task cancellation, e.g. systemd stopping the unit)
            # lands here, not in the generic `Exception` branch below --
            # deliberately distinguished with a "graceful_shutdown:"
            # prefix so a caller (or a human reading the heartbeat) can
            # tell "the operator stopped this on purpose" apart from "this
            # genuinely crashed." Still finalizes to a real terminal
            # report, still never re-raises -- the same "run() never
            # raises" contract this method has always had.
            errors.append("graceful_shutdown: cancellation received")
        except Exception as exc:  # noqa: BLE001 -- run() must never raise; every failure is recorded, not propagated.
            errors.append(f"unexpected_failure: {type(exc).__name__}: {exc}")

        final_stage = DailySessionStage.FAILED if errors else DailySessionStage.SESSION_COMPLETE
        if self._lifecycle is not None and not self._lifecycle.is_terminal():
            self._advance(final_stage, reason="; ".join(errors) if errors else "")

        return DailySessionReport(
            session_date=self._session_date, final_stage=self._lifecycle.current_stage.value,
            capture_result=capture_result, intelligence_result=intelligence_result,
            errors=tuple(errors), lifecycle=self._lifecycle, completeness_result=completeness_result,
        )
