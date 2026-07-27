"""Qualification Recorder — BUJJI Options OS v3, Engineering Series 58.

Collects one immutable `QualificationRecord` per replay session. The
recorder itself is the one genuinely stateful object in the historical
qualification framework -- its entire purpose is accumulation -- but
every record it holds is a frozen dataclass, and `record()` only ever
appends: nothing already recorded can be overwritten or removed.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Tuple

RUNTIME_OUTCOME_COMPLETED = "COMPLETED"
RUNTIME_OUTCOME_REJECTED_CIRCUIT = "REJECTED_CIRCUIT"
RUNTIME_OUTCOME_REJECTED_RATE_LIMIT = "REJECTED_RATE_LIMIT"
RUNTIME_OUTCOME_FAILED = "FAILED"

ALL_RUNTIME_OUTCOMES = (
    RUNTIME_OUTCOME_COMPLETED,
    RUNTIME_OUTCOME_REJECTED_CIRCUIT,
    RUNTIME_OUTCOME_REJECTED_RATE_LIMIT,
    RUNTIME_OUTCOME_FAILED,
)


@dataclass(frozen=True)
class RuntimeOutcome:
    """A small, immutable classification of what happened to one
    replay session's admission attempt -- never a raw dump of the
    underlying `ShadowResult`, but always traceable to it via
    `shadow_result`.
    """

    status: str
    reason: str
    shadow_result: Optional[Any]


@dataclass(frozen=True)
class QualificationRecord:
    """One immutable record of one replay session's full journey
    through the admission-control chain and, if admitted, the Shadow
    Runtime. Every field here is either the evidence itself (the exact
    `RuntimeHealthSnapshot`/`CircuitDecision`/`RateLimitDecision`
    objects produced for this session -- never copies, never
    summaries) or a plain identifier.
    """

    replay_identifier: str
    timestamp: str
    strategy_decision: Optional[Any]
    risk_decision: Optional[Any]
    capital_decision: Optional[Any]
    execution_plan: Optional[Any]
    health_snapshot: Any
    circuit_decision: Any
    rate_limit_decision: Any
    runtime_outcome: RuntimeOutcome
    qualification_fingerprint: str
    # Series 69 Phase 2 (Qualification Observability Expansion,
    # recording-only): both fields are copied verbatim from values the
    # pipeline already computed elsewhere -- never derived, never
    # inferred. `market_state_assessment` mirrors `strategy_decision`'s
    # own existing `getattr(shadow_result, ..., None)` extraction
    # pattern at the construction site
    # (`historical_runner.py::run_corpus`). `scenario` is the source
    # `ReplayScenario` this record was built from, which already
    # carries the classification fields (`market_context`,
    # `market_opinion`, `context_stability`, `calibration`,
    # `governance`, `lifecycle`, `contract`) `report.py`'s new
    # `sessions[]` entries read from -- no new field is needed for
    # those, since they are already reachable via `scenario`.
    market_state_assessment: Optional[Any] = None
    scenario: Optional[Any] = None
    # Series 69 Phase 2b (deep pass, recording-only): copied verbatim
    # from `shadow_result.evidence_interpretation` (see
    # production_runtime/runtime.py ShadowResult), exactly mirroring
    # the `market_state_assessment` extraction pattern above -- never
    # derived, never inferred.
    evidence_interpretation: Optional[Any] = None
    # Engineering Series 70 Phase 2 (Context Stability Observatory,
    # recording-only): the full `PublishedState` (bujji.mic_replay.
    # publication_replay.PublishedState) this session's `scenario` was
    # originally flattened from, when the caller of `run_corpus()`
    # (historical_runner.py) has one available -- attached directly,
    # mirroring how `scenario` itself was retained in Series 69 Phase 2,
    # since `ReplayScenario`/`HistoricalSessionRecord` do not carry the
    # new Context Stability detail fields and extending that whole chain
    # is out of scope for a recording-only sprint. `None` when the caller
    # does not supply `published_states` to `run_corpus()` (backward
    # compatible -- no existing caller is affected).
    published_state: Optional[Any] = None


class QualificationRecorder:
    """Append-only. `records` is always returned as a fresh tuple
    snapshot -- callers can never mutate the recorder's own internal
    storage through it.
    """

    def __init__(self) -> None:
        self._records: list = []

    def record(self, record: QualificationRecord) -> None:
        self._records.append(record)

    @property
    def records(self) -> Tuple[QualificationRecord, ...]:
        return tuple(self._records)

    def __len__(self) -> int:
        return len(self._records)
