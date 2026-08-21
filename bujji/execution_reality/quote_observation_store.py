"""Quote Observation Store -- Execution Reality Layer, Phase-0.

Simplest possible append-only storage: one JSON object per line
(JSONL), human-readable, reviewable with any text editor, no
database. Mirrors the storage SHAPE Shadow Observatory's own
SessionStore already uses (JSONL, append-only) for consistency with
this codebase's established conventions -- but is a fully independent
implementation with NO import of shadow_observatory, per this phase's
explicit scope boundary (no Shadow Observatory integration yet).

Every write is wrapped in its own try/except, recording to
`self.write_errors` and NEVER raising -- a storage failure must not be
able to crash whatever (eventually) calls this, matching the same
fire-and-forget-safe discipline ShadowObservatoryRecorder/SessionStore
already established for exactly this reason.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import List

from bujji.core.enums import Side

from .models import LegQuote, QuoteObservationRecord


def build_quote_observation_record(
    contract, side: Side, exchange: str, broker_source: str, leg_quote: LegQuote,
    raw_source_status: str, recorded_at: str,
) -> QuoteObservationRecord:
    """Wraps a MarketQuoteAdapter.fetch() result into the forensic
    artifact. `recorded_at` is the ARTIFACT storage time -- distinct
    from `leg_quote.timestamp`, which is the broker OBSERVATION time.
    The two are almost always a few milliseconds apart; if they were
    ever identical or reversed, that itself is a bug signal (see the
    Phase-0 validation plan's own timestamp-ordering check)."""
    return QuoteObservationRecord(
        symbol=contract.symbol, exchange=exchange, expiry=contract.expiry, strike=float(contract.strike),
        option_type=contract.option_type.value, side=side, timestamp=recorded_at, broker_source=broker_source,
        raw_source_status=raw_source_status, normalized_result=leg_quote,
    )


class QuoteObservationStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.write_errors: List[str] = []
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self.write_errors.append(f"mkdir failed: {exc}")

    def append(self, record: QuoteObservationRecord) -> None:
        try:
            # Side is a (str, Enum) subclass -- json.dumps serializes it directly
            # via its str value (verified: json.dumps({"side": Side.BUY}) == '{"side": "BUY"}'),
            # no manual conversion needed, including for the nested normalized_result.side.
            payload = asdict(record)
            with open(self.path, "a") as f:
                f.write(json.dumps(payload, default=str) + "\n")
        except Exception as exc:  # noqa: BLE001 -- a storage failure must never propagate, per this module's own docstring
            self.write_errors.append(f"append failed for {record.symbol}: {exc}")

    def read_all(self) -> List[dict]:
        """Recovery/review helper -- reads every observation back as
        plain dicts (not reconstructed dataclasses; this is a review
        tool, not a live-state loader, and Phase-0 has no consumer
        that needs typed objects back)."""
        if not self.path.exists():
            return []
        records = []
        with open(self.path) as f:
            for line in f:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
        return records
