"""Real Broker Calibration Runner — BUJJI Options OS v3, Numeric Risk
Governor Gate C.5 (READ-ONLY, HUMAN-TRIGGERED).

PURPOSE, AND WHAT THIS PHASE'S OUTPUT IS NOT: collects controlled,
real-world broker margin observations and feeds them into the
existing certification pipeline. The output is CALIBRATION EVIDENCE --
a set of recorded observations plus, optionally, a certification
result computed from them. It is NOT trade authorization. Nothing in
this module has a reference to CapitalCheckInput, never imports
capital_check.py, and no sample or run result is ever consumed by
anything that gates a trade. capital_check.assess_capital() remains
the sole ALLOW/VETO authority, completely untouched, exactly as it has
been by every prior Gate C phase.

EXPLICIT INVOCATION ONLY -- NO BACKGROUND EXECUTION OF ANY KIND:
capture_sample() must be called directly by an operator-driven script
or REPL, once per real observation. There is no scheduler, no daemon,
no background thread, no asyncio task spawned by this module, and no
automatic polling loop anywhere in this file -- verified structurally
(see tests/test_margin_calibration_runner.py::
test_no_background_execution_primitives_anywhere, an AST scan for
threading/asyncio.create_task/schedule/cron-shaped names).

PERSISTENCE, REUSING THE ESTABLISHED CONVENTION RATHER THAN INVENTING
A NEW ONE: MarginCalibrationStore mirrors bujji.msi_trade_construction.
journal.TradeConstructionJournal's own shape exactly -- an in-memory,
append-only list of immutable (frozen dataclass) entries, with a
samples() accessor returning a fresh, immutable tuple. That module's
own docstring states it "mirrors every prior MSI package's journal
convention," confirming this is the actual established pattern across
this codebase, not a bespoke choice. Gate A's SQLite-backed
PositionGroupJournal was deliberately NOT reused here -- that
machinery exists for crash-recoverable LIVE position tracking, a
materially higher-stakes concern than this phase's human-triggered,
read-only calibration capture. `export_samples()` is provided for
easy analysis, per this phase's own requirement, without adding any
new file-format/database dependency.

REUSE, NOT DUPLICATION, OF EVERY EXISTING RULE: this module performs
zero margin math, zero broker comparison logic, and zero certification
logic of its own. It calls SimulatedMarginProvider.get_portfolio_margin
(C.1), FyersMarginProvider.get_broker_margin_snapshot (C.3),
margin_comparison_engine.compare_margin (C.3), and
MarginCertificationEngine.certify (C.4) -- exactly as they already
exist, unmodified.

SCOPE LIMITATION INHERITED FROM C.3, DOCUMENTED AGAIN HERE: the broker
side of a calibration sample can only ever be ONE CE+PE pair (that is
the real, certified FyersBroker.get_order_margin() shape), while the
simulated side accepts an arbitrary N-leg book. For a calibration
sample to be a genuinely apples-to-apples comparison, the `legs`
supplied to capture_sample() should represent the SAME 2-leg structure
as `ce_contract`/`pe_contract` -- this module does not and cannot
verify that correspondence (it has no way to know if a caller's leg
list "matches" a contract pair beyond what compare_margin already
checks), so correct sample construction is the operator's
responsibility. Documented here rather than silently assumed."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

from bujji.core.models import OptionContract
from bujji.trading_brain.risk_governor.broker_margin_reality_adapter import BrokerMarginSnapshot, FyersMarginProvider
from bujji.trading_brain.risk_governor.margin_certification_engine import (
    MarginCertificationCase,
    MarginCertificationEngine,
    MarginCertificationResult,
)
from bujji.trading_brain.risk_governor.margin_comparison_engine import MarginComparisonReport, compare_margin
from bujji.trading_brain.risk_governor.simulated_margin_provider import SimulatedMarginProvider
from bujji.trading_brain.risk_governor.whole_book_margin_provider import MarginLegRequest, MarginSnapshot

Clock = Callable[[], datetime]

STATUS_CAPTURED = "CAPTURED"
STATUS_SAMPLE_NOT_CAPTURED = "SAMPLE_NOT_CAPTURED"


class DuplicateCalibrationSampleError(Exception):
    """Raised when a sample_id already exists in the store -- historical
    calibration samples are immutable and can never be overwritten."""


@dataclass(frozen=True)
class MarginCalibrationSample:
    """One real observation. Immutable once constructed -- every field
    is either a primitive, a frozen dataclass, or an immutable tuple."""

    sample_id: str
    timestamp: datetime
    strategy_type: str
    position_description: str
    margin_leg_input: Tuple[MarginLegRequest, ...]
    simulated_margin_snapshot: MarginSnapshot
    broker_margin_snapshot: BrokerMarginSnapshot
    comparison_report: MarginComparisonReport
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CalibrationCaptureResult:
    """Returned by every capture_sample() call. `captured=False` means
    NOTHING was written to the store -- never a half-recorded or
    zero-margin sample."""

    captured: bool
    sample: Optional[MarginCalibrationSample]
    status: str                # CAPTURED | SAMPLE_NOT_CAPTURED
    reason: Optional[str]


class MarginCalibrationStore:
    """Append-only, in-memory. Mirrors TradeConstructionJournal's own
    shape (see module docstring). No method anywhere on this class can
    modify or remove an already-recorded sample -- there is no
    update/delete/clear method at all, only record_sample() and
    read-only accessors."""

    def __init__(self) -> None:
        self._samples_by_id: Dict[str, MarginCalibrationSample] = {}

    def record_sample(self, sample: MarginCalibrationSample) -> None:
        if sample.sample_id in self._samples_by_id:
            raise DuplicateCalibrationSampleError(
                f"sample_id {sample.sample_id!r} already recorded -- historical calibration "
                "samples are immutable and cannot be overwritten"
            )
        self._samples_by_id[sample.sample_id] = sample

    def samples(self) -> Tuple[MarginCalibrationSample, ...]:
        return tuple(self._samples_by_id.values())

    def get(self, sample_id: str) -> Optional[MarginCalibrationSample]:
        return self._samples_by_id.get(sample_id)

    def export_samples(self) -> Tuple[Dict[str, Any], ...]:
        """Easy export for analysis -- flat, JSON-shaped dicts, one per
        sample, in recorded order."""
        return tuple(
            {
                "sample_id": s.sample_id,
                "timestamp": s.timestamp.isoformat(),
                "strategy_type": s.strategy_type,
                "position_description": s.position_description,
                "simulated_margin": s.simulated_margin_snapshot.required_margin,
                "broker_margin": s.broker_margin_snapshot.required_margin,
                "comparison_status": s.comparison_report.status,
                "deviation_fraction": s.comparison_report.deviation_fraction,
                "metadata": dict(s.metadata),
            }
            for s in self.samples()
        )

    def __len__(self) -> int:
        return len(self._samples_by_id)


class MarginCalibrationRunner:
    """Human-triggered only -- see module docstring. Every call to
    capture_sample() is one explicit, operator-initiated observation;
    nothing in this class schedules, retries, or repeats a capture on
    its own."""

    def __init__(
        self, simulated_provider: SimulatedMarginProvider, broker_provider: FyersMarginProvider,
        store: MarginCalibrationStore,
    ) -> None:
        self._simulated_provider = simulated_provider
        self._broker_provider = broker_provider
        self._store = store

    async def capture_sample(
        self,
        sample_id: str,
        strategy_type: str,
        position_description: str,
        legs: List[MarginLegRequest],
        ce_contract: OptionContract,
        pe_contract: OptionContract,
        clock: Clock,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> CalibrationCaptureResult:
        if not sample_id or not sample_id.strip():
            return self._rejected(None, "sample_id is required and cannot be empty")
        if not strategy_type or not strategy_type.strip():
            return self._rejected(
                sample_id, "strategy_type is required -- pass the literal string 'UNKNOWN' "
                "explicitly if this sample is genuinely unclassifiable, never leave it blank"
            )
        if self._store.get(sample_id) is not None:
            return self._rejected(sample_id, f"sample_id {sample_id!r} already recorded")

        simulated_snapshot = self._simulated_provider.get_portfolio_margin(legs, clock=clock)
        if not simulated_snapshot.margin_verified or simulated_snapshot.required_margin is None:
            return self._rejected(sample_id, "simulated margin snapshot failed validation -- partial data rejected")

        broker_snapshot = await self._broker_provider.get_broker_margin_snapshot(ce_contract, pe_contract, clock=clock)
        if not broker_snapshot.available:
            # Step 7: broker unavailable -> SAMPLE_NOT_CAPTURED, never margin=0.
            return self._rejected(sample_id, f"broker margin snapshot unavailable (source={broker_snapshot.source})")

        comparison_report = compare_margin(simulated_snapshot, broker_snapshot, clock=clock)

        sample = MarginCalibrationSample(
            sample_id=sample_id, timestamp=clock(), strategy_type=strategy_type,
            position_description=position_description, margin_leg_input=tuple(legs),
            simulated_margin_snapshot=simulated_snapshot, broker_margin_snapshot=broker_snapshot,
            comparison_report=comparison_report, metadata=metadata or {},
        )

        try:
            self._store.record_sample(sample)
        except DuplicateCalibrationSampleError as exc:
            return self._rejected(sample_id, str(exc))

        return CalibrationCaptureResult(captured=True, sample=sample, status=STATUS_CAPTURED, reason=None)

    @staticmethod
    def _rejected(sample_id: Optional[str], reason: str) -> CalibrationCaptureResult:
        return CalibrationCaptureResult(
            captured=False, sample=None, status=STATUS_SAMPLE_NOT_CAPTURED, reason=reason,
        )


@dataclass(frozen=True)
class CalibrationRun:
    run_id: str
    created_at: datetime
    samples: Tuple[MarginCalibrationSample, ...]
    certification_result: Optional[MarginCertificationResult]
    operator_metadata: Dict[str, Any] = field(default_factory=dict)


def build_certification_result_from_samples(
    samples: List[MarginCalibrationSample], engine: MarginCertificationEngine,
) -> MarginCertificationResult:
    """Converts calibration samples into MarginCertificationCase
    objects (the EXISTING C.4 data model, never redefined) and calls
    the EXISTING engine.certify() -- zero certification rules
    duplicated here."""
    cases = [
        MarginCertificationCase(
            case_id=s.sample_id, strategy_type=s.strategy_type,
            simulated_margin_snapshot=s.simulated_margin_snapshot,
            broker_margin_snapshot=s.broker_margin_snapshot,
            comparison_report=s.comparison_report, timestamp=s.timestamp, metadata=dict(s.metadata),
        )
        for s in samples
    ]
    return engine.certify(cases)


def build_calibration_run(
    run_id: str, store: MarginCalibrationStore, clock: Clock,
    engine: Optional[MarginCertificationEngine] = None,
    operator_metadata: Optional[Dict[str, Any]] = None,
) -> CalibrationRun:
    samples = store.samples()
    # MarginCertificationEngine.certify() already handles an empty list correctly
    # (returns INSUFFICIENT_DATA) -- no special-casing needed here.
    certification_result = build_certification_result_from_samples(
        list(samples), engine or MarginCertificationEngine(),
    )
    return CalibrationRun(
        run_id=run_id, created_at=clock(), samples=samples,
        certification_result=certification_result, operator_metadata=operator_metadata or {},
    )
