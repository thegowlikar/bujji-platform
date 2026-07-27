"""Replay Corpus Validator — BUJJI Options OS v3, Engineering Series 59
(schema evolved by Series 64).

Verifies raw historical session records before they become part of a
replay corpus. Never repairs, fills, or fabricates missing data --
an incomplete or corrupted session is flagged and excluded, and the
reason is always recorded, never silently dropped.

`HistoricalSessionRecord`'s canonical definition moved to
`historical_session.py` in Series 64 (schema expansion for optional
OI/bid-ask/VIX/session-metadata transport fields) and is re-exported
here unchanged, so every existing importer of
`bujji.replay.validator.HistoricalSessionRecord` -- Data Acquisition
Sprint A's `option_chain_ingestion.py`, `corpus_builder.py`, and every
existing test -- continues to work with no change. Validation logic
below is unchanged from Series 59: it inspects only the original v1
fields and never reads any Series 64 field, per this sprint's own
"transport only, never consumed" principle.
"""
from __future__ import annotations

from datetime import datetime
from typing import Callable, Sequence, Tuple

from dataclasses import dataclass

from .historical_session import HistoricalSessionRecord

VALID_OPTION_TYPES = ("CE", "PE")

Clock = Callable[[], datetime]


def _real_clock() -> datetime:
    return datetime.now()


@dataclass(frozen=True)
class SessionValidationResult:
    session_id: str
    valid: bool
    issues: Tuple[str, ...]


@dataclass(frozen=True)
class CorpusValidationReport:
    total_sessions: int
    valid_sessions: int
    invalid_sessions: int
    session_results: Tuple[SessionValidationResult, ...]
    timestamp: str


def validate_session(record: HistoricalSessionRecord) -> SessionValidationResult:
    """Check one session record. Never mutates `record`. Every rule
    below checks something the record either has or does not have --
    no inference, no repair, no default substitution.
    """
    issues = []

    session_id = record.session_id or ""
    if not session_id:
        issues.append("missing deterministic session identifier")

    if not record.timestamp:
        issues.append("missing timestamp")
    else:
        try:
            datetime.fromisoformat(record.timestamp)
        except ValueError:
            issues.append(f"timestamp is not a valid ISO-8601 string: {record.timestamp!r}")

    if not record.trading_date:
        issues.append("missing trading_date")

    if record.spot is None:
        issues.append("missing spot snapshot (spot is None)")
    elif record.spot <= 0:
        issues.append(f"spot snapshot is not positive: {record.spot!r}")

    if not record.option_chain_entries:
        issues.append("missing option chain (no entries)")
    else:
        declared_expiries = set(record.option_chain_expiries)
        for strike, option_type, expiry, symbol in record.option_chain_entries:
            if expiry not in declared_expiries:
                issues.append(f"option chain entry expiry {expiry!r} is not declared in option_chain_expiries")
            if option_type not in VALID_OPTION_TYPES:
                issues.append(f"option chain entry has unrecognized option_type: {option_type!r}")
            if strike is None or strike <= 0:
                issues.append(f"option chain entry has invalid strike: {strike!r}")
            if not symbol:
                issues.append(f"option chain entry at strike {strike} has no contract_symbol")

    return SessionValidationResult(
        session_id=session_id or "UNKNOWN",
        valid=(len(issues) == 0),
        issues=tuple(issues),
    )


def validate_corpus(
    records: Sequence[HistoricalSessionRecord], clock: Clock = _real_clock
) -> CorpusValidationReport:
    """Validate every record independently. Order of `records` is
    preserved in `session_results` -- this function never sorts,
    reorders, or deduplicates.
    """
    results = tuple(validate_session(r) for r in records)
    valid_count = sum(1 for r in results if r.valid)
    return CorpusValidationReport(
        total_sessions=len(results),
        valid_sessions=valid_count,
        invalid_sessions=len(results) - valid_count,
        session_results=results,
        timestamp=clock().isoformat(),
    )
