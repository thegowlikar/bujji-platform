"""Decision Auditor & Learning Observatory runner — Series 99. Dual
batch/streaming entrypoints, proven byte-identical by test (house
convention)."""
from __future__ import annotations

from typing import List, Sequence, Tuple

from .engine import build_decision_record, build_outcome_record, link
from .journal import DecisionAuditorJournal
from .models import DecisionOutcomePair, DecisionRecord, OutcomeRecord


def build_decisions_batch(requests: Sequence[dict]) -> Tuple[DecisionRecord, ...]:
    return tuple(build_decision_record(**req) for req in requests)


def build_outcomes_batch(requests: Sequence[dict]) -> Tuple[OutcomeRecord, ...]:
    return tuple(build_outcome_record(**req) for req in requests)


class DecisionAuditorStream:
    def __init__(self) -> None:
        self.journal = DecisionAuditorJournal()
        self._decisions: List[DecisionRecord] = []
        self._outcomes: List[OutcomeRecord] = []
        self._pairs: List[DecisionOutcomePair] = []

    def submit_decision(self, **kwargs) -> DecisionRecord:
        record = build_decision_record(**kwargs)
        self._decisions.append(record)
        self.journal.record_decision(record, recorded_at=kwargs["timestamp"])
        return record

    def submit_outcome(self, **kwargs) -> OutcomeRecord:
        record = build_outcome_record(**kwargs)
        self._outcomes.append(record)
        self.journal.record_outcome(record, recorded_at=kwargs["timestamp"])
        return record

    def submit_pair(self, decision: DecisionRecord, outcome: OutcomeRecord) -> DecisionOutcomePair:
        pair = link(decision, outcome)
        self._pairs.append(pair)
        self.journal.record_pair(pair, recorded_at=outcome.timestamp)
        return pair

    def decisions(self) -> Tuple[DecisionRecord, ...]:
        return tuple(self._decisions)

    def outcomes(self) -> Tuple[OutcomeRecord, ...]:
        return tuple(self._outcomes)

    def pairs(self) -> Tuple[DecisionOutcomePair, ...]:
        return tuple(self._pairs)
