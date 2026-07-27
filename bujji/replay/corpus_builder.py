"""Historical Replay Corpus Builder — BUJJI Options OS v3, Engineering
Series 59.

Turns a sequence of raw `HistoricalSessionRecord`s into a canonical,
versioned replay corpus: a tuple of Series 46 `ReplayScenario`s plus
their deterministic timestamps, exactly the `(scenarios, timestamps)`
shape `bujji.qualification.historical_runner.HistoricalQualificationRunner.run_corpus()`
(Series 58) already consumes -- unmodified.

This module builds data, not trading logic. It never invents,
interpolates, or repairs a spot price, an option chain entry, or a
timestamp; it never reorders sessions except to restore original
chronology when records arrive out of order; it never places an
order, and it never imports anything from `bujji.production_runtime`,
`bujji.trading_brain`, `bujji.runtime_execution`, or any broker
module.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Optional, Sequence, Tuple

from ..qualification.replay_models import ReplayScenario
from ..trading_brain.nifty_contract_builder.models import (
    NiftyOptionChainEntry,
    NiftyOptionChainSnapshot,
    NiftySpotSnapshot,
)
from .manifest import CorpusManifest, build_manifest
from .validator import CorpusValidationReport, HistoricalSessionRecord, validate_corpus

Clock = Callable[[], datetime]


def _real_clock() -> datetime:
    return datetime.now()


@dataclass(frozen=True)
class CorpusBuildResult:
    """Immutable. `scenarios`/`timestamps` are exactly the two
    sequences Series 58's `run_corpus()` expects, built only from
    sessions that passed validation and were not on an exchange
    holiday -- `excluded_holiday_session_ids` and
    `validation_report.session_results` together account for every
    input record.
    """

    manifest: CorpusManifest
    scenarios: Tuple[ReplayScenario, ...]
    timestamps: Tuple[datetime, ...]
    validation_report: CorpusValidationReport
    excluded_holiday_session_ids: Tuple[str, ...]


def _record_to_scenario(record: HistoricalSessionRecord) -> ReplayScenario:
    spot_snapshot: Optional[NiftySpotSnapshot] = None
    if record.spot is not None:
        spot_snapshot = NiftySpotSnapshot(spot=record.spot, as_of=record.spot_as_of or record.timestamp)

    option_chain: Optional[NiftyOptionChainSnapshot] = None
    if record.option_chain_entries:
        entries = tuple(
            NiftyOptionChainEntry(strike=strike, option_type=option_type, expiry=expiry, contract_symbol=symbol)
            for strike, option_type, expiry, symbol in record.option_chain_entries
        )
        option_chain = NiftyOptionChainSnapshot(
            expiries=record.option_chain_expiries,
            entries=entries,
            as_of=record.option_chain_as_of or record.timestamp,
        )

    return ReplayScenario(
        scenario_id=record.session_id,
        description=f"Historical session {record.session_id} ({record.trading_date})",
        market_context=record.market_context,
        market_opinion=record.market_opinion,
        context_stability=record.context_stability,
        calibration=record.calibration,
        governance=record.governance,
        lifecycle=record.lifecycle,
        contract=record.contract,
        spot_snapshot=spot_snapshot,
        option_chain=option_chain,
    )


def compute_checksum(records: Sequence[HistoricalSessionRecord]) -> str:
    """Deterministic checksum over the exact session content included
    in a corpus -- `hashlib.sha256`, not a fast/non-cryptographic
    hash, so accidental collisions across genuinely different corpora
    are effectively impossible. Order-sensitive by design: a corpus
    with the same sessions in a different order is a different
    corpus, and must checksum differently, since chronology is part of
    what this sprint preserves.
    """
    parts = []
    for r in records:
        entries_repr = "|".join(
            f"{strike}:{option_type}:{expiry}:{symbol}"
            for strike, option_type, expiry, symbol in r.option_chain_entries
        )
        parts.append(
            f"{r.session_id}#{r.trading_date}#{r.timestamp}#{r.spot}#"
            f"{','.join(r.option_chain_expiries)}#{entries_repr}"
        )
    seed = "CORPUS_CHECKSUM|" + "||".join(parts)
    return hashlib.sha256(seed.encode()).hexdigest()


def build_corpus(
    records: Sequence[HistoricalSessionRecord],
    source_description: str,
    holidays: Tuple[str, ...] = (),
    clock: Clock = _real_clock,
) -> CorpusBuildResult:
    """Build a canonical corpus from raw session records.

    Steps, in order:
    1. Exclude any record whose `trading_date` is a declared holiday,
       or whose own `is_holiday` flag is set -- recorded, never
       silently dropped.
    2. Validate every remaining record (`validator.validate_corpus`).
       Invalid sessions are excluded from the emitted corpus but
       remain fully visible in `validation_report.session_results` --
       never repaired, never guessed.
    3. Restore original chronology: sort the valid, non-holiday
       records by `timestamp` ascending. Input order is not assumed to
       already be chronological; this step never invents a timestamp,
       it only reorders records that already carry one.
    4. Convert each surviving record to a `ReplayScenario` (data only
       -- no trading logic) and its own `datetime` timestamp.
    5. Compute a checksum over exactly the surviving records and build
       the corpus manifest.
    """
    holiday_dates = set(holidays)
    non_holiday_records = []
    excluded_holiday_ids = []
    for r in records:
        if r.is_holiday or r.trading_date in holiday_dates:
            excluded_holiday_ids.append(r.session_id)
        else:
            non_holiday_records.append(r)

    validation_report = validate_corpus(non_holiday_records, clock=clock)
    valid_ids = {res.session_id for res in validation_report.session_results if res.valid}
    valid_records = [r for r in non_holiday_records if r.session_id in valid_ids]

    ordered_records = sorted(valid_records, key=lambda r: r.timestamp)

    scenarios = tuple(_record_to_scenario(r) for r in ordered_records)
    timestamps = tuple(datetime.fromisoformat(r.timestamp) for r in ordered_records)

    checksum = compute_checksum(ordered_records)
    trading_dates = tuple(sorted({r.trading_date for r in ordered_records}))
    manifest = build_manifest(
        source_description=source_description,
        trading_dates=trading_dates,
        session_count=len(ordered_records),
        checksum=checksum,
        clock=clock,
    )

    return CorpusBuildResult(
        manifest=manifest,
        scenarios=scenarios,
        timestamps=timestamps,
        validation_report=validation_report,
        excluded_holiday_session_ids=tuple(excluded_holiday_ids),
    )
