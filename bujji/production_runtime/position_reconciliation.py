"""Continuous position reconciliation: what Bujji believes vs what the broker holds.

WHAT THIS CLOSES. Broker truth was consulted at three moments -- when an order
is placed, at startup, and at EOD -- and never in between. During a session the
only position read went through PositionRealityRegistry, which INTERSECTS the
broker's positions with an in-memory table of registered symbols. A position at
a symbol Bujji never registered is therefore not merely unread: it is
mathematically undiscoverable through that API. It is never valued, never
stop-lossed, never escalated, and nothing notices until EOD.

This runs an UNFILTERED read on the existing management cadence and compares.

SOURCE-OF-TRUTH HIERARCHY, enforced here:
    BROKER TRUTH  >  durable journal  >  derived state  >  memory cache
The broker always wins. A divergence is never resolved by preferring memory.

WHAT IS AND IS NOT COMPARED, stated precisely. PositionRealityRegistry stores
each group's SYMBOLS but deliberately not its quantities -- its own docstring
says "quantity/price/PnL are still always re-read from PaperBroker on demand,
never cached here", which correctly avoids a stale cached figure. So Bujji has
no independent expected QUANTITY to compare against, and this module does not
invent one: symbol-level presence is compared, and quantity is reported as
observed-only rather than as a match or a mismatch. Manufacturing an expected
quantity here would recreate exactly the stale cache the registry avoids, and
a fabricated comparison is worse than a declared gap.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

# Per-symbol findings.
MATCH = "MATCH"
BROKER_ONLY = "BROKER_ONLY"          # exposure Bujji does not know about
EXPECTED_ONLY = "EXPECTED_ONLY"      # Bujji believes it holds this; broker does not show it

# Overall verdicts.
RECONCILED = "RECONCILED"
DIVERGED = "DIVERGED"
UNKNOWN = "UNKNOWN"

# Severities. Risk-INCREASING divergence outranks risk-decreasing: an
# unexpected live position can lose money, a stale belief cannot.
SEVERITY_NONE = "NONE"
SEVERITY_WARNING = "WARNING"
SEVERITY_CRITICAL = "CRITICAL"

REASON_UNEXPECTED_EXPOSURE = "CRITICAL_UNEXPECTED_EXPOSURE"
REASON_BELIEF_WITHOUT_POSITION = "BELIEF_WITHOUT_BROKER_POSITION"
REASON_BROKER_UNREADABLE = "BROKER_TRUTH_UNREADABLE"


@dataclass(frozen=True)
class SymbolFinding:
    symbol: str
    finding: str
    observed_qty: Optional[int] = None
    observed_side: Optional[str] = None
    detail: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"symbol": self.symbol, "finding": self.finding,
                "observed_qty": self.observed_qty, "observed_side": self.observed_side,
                "detail": self.detail}


@dataclass(frozen=True)
class ReconciliationResult:
    verdict: str
    severity: str
    findings: Tuple[SymbolFinding, ...] = ()
    expected_symbols: Tuple[str, ...] = ()
    observed_symbols: Tuple[str, ...] = ()
    detail: str = ""
    # Declared, not silently omitted -- see the module docstring.
    quantity_compared: bool = False
    quantity_not_compared_reason: str = (
        "no independent expected quantity exists: PositionRealityRegistry stores "
        "symbols but deliberately never caches quantity")

    @property
    def blocks_new_risk(self) -> bool:
        """New risk is refused on CRITICAL divergence and on UNKNOWN.

        UNKNOWN blocks because a read we could not perform is not evidence of
        safety -- taking new risk on top of an unverifiable book is the one
        thing a restart or a degraded broker must never allow.
        """
        return self.severity == SEVERITY_CRITICAL or self.verdict == UNKNOWN

    def to_dict(self) -> Dict[str, Any]:
        return {
            "verdict": self.verdict,
            "severity": self.severity,
            "blocks_new_risk": self.blocks_new_risk,
            "findings": [f.to_dict() for f in self.findings],
            "expected_symbols": list(self.expected_symbols),
            "observed_symbols": list(self.observed_symbols),
            "detail": self.detail,
            "quantity_compared": self.quantity_compared,
            "quantity_not_compared_reason": self.quantity_not_compared_reason,
        }


def reconcile(expected_symbols: Set[str],
              observed_positions: Optional[Sequence[Dict[str, Any]]]) -> ReconciliationResult:
    """Compare belief against broker truth. Pure -- no I/O, no clock.

    `observed_positions is None` means the read FAILED. That is UNKNOWN, never
    an empty book: collapsing a failed read into "nothing there" is the single
    most dangerous transformation available, because it reads as flat.
    """
    if observed_positions is None:
        return ReconciliationResult(
            verdict=UNKNOWN, severity=SEVERITY_CRITICAL,
            expected_symbols=tuple(sorted(expected_symbols)),
            detail=REASON_BROKER_UNREADABLE)

    observed: Dict[str, Dict[str, Any]] = {}
    for row in observed_positions:
        symbol = row.get("symbol")
        if not symbol:
            continue
        try:
            qty = int(row.get("qty", 0) or 0)
        except (TypeError, ValueError):
            # A row we cannot parse is not evidence of absence.
            return ReconciliationResult(
                verdict=UNKNOWN, severity=SEVERITY_CRITICAL,
                expected_symbols=tuple(sorted(expected_symbols)),
                detail=f"{REASON_BROKER_UNREADABLE}: malformed row {row!r}")
        if qty > 0:
            observed[str(symbol)] = {"qty": qty, "side": row.get("side")}

    findings: List[SymbolFinding] = []
    for symbol in sorted(set(observed) - set(expected_symbols)):
        findings.append(SymbolFinding(
            symbol=symbol, finding=BROKER_ONLY,
            observed_qty=observed[symbol]["qty"], observed_side=observed[symbol]["side"],
            detail=REASON_UNEXPECTED_EXPOSURE))
    for symbol in sorted(set(expected_symbols) - set(observed)):
        findings.append(SymbolFinding(
            symbol=symbol, finding=EXPECTED_ONLY,
            detail=REASON_BELIEF_WITHOUT_POSITION))
    for symbol in sorted(set(observed) & set(expected_symbols)):
        findings.append(SymbolFinding(
            symbol=symbol, finding=MATCH,
            observed_qty=observed[symbol]["qty"], observed_side=observed[symbol]["side"]))

    unexpected = [f for f in findings if f.finding == BROKER_ONLY]
    stale = [f for f in findings if f.finding == EXPECTED_ONLY]

    if unexpected:
        # RISK-INCREASING. The broker holds exposure Bujji is not managing.
        return ReconciliationResult(
            verdict=DIVERGED, severity=SEVERITY_CRITICAL, findings=tuple(findings),
            expected_symbols=tuple(sorted(expected_symbols)),
            observed_symbols=tuple(sorted(observed)),
            detail=f"{REASON_UNEXPECTED_EXPOSURE}: " + ", ".join(
                f"{f.symbol}x{f.observed_qty}" for f in unexpected))
    if stale:
        # RISK-DECREASING. Bujji believes in a position the broker does not
        # show -- most often a completed exit. Worth surfacing, not a stop.
        return ReconciliationResult(
            verdict=DIVERGED, severity=SEVERITY_WARNING, findings=tuple(findings),
            expected_symbols=tuple(sorted(expected_symbols)),
            observed_symbols=tuple(sorted(observed)),
            detail=f"{REASON_BELIEF_WITHOUT_POSITION}: " + ", ".join(f.symbol for f in stale))
    return ReconciliationResult(
        verdict=RECONCILED, severity=SEVERITY_NONE, findings=tuple(findings),
        expected_symbols=tuple(sorted(expected_symbols)),
        observed_symbols=tuple(sorted(observed)),
        detail=f"{len(observed)} symbol(s) agree")
