"""The hard data-quality boundary in front of trading.

WHAT WAS MISSING. Nothing stood between market data and a trading decision.
A forensic trace of the trading runner for any quality gate returned exactly
one string, in one branch, for one condition:

    "-- refusing to trade on an absent book rather than falling back to
     stale data."

No source health, no completeness, no freshness, no provenance. Meanwhile
`MarketDataAdapter.build_snapshot()` was ALREADY computing `health_status`
(OK / DEGRADED / UNAVAILABLE) and `missing_fields` on every snapshot, and
`IntelligenceCycleRecorder` was already recording the value under
"market_snapshot_health" -- where nothing read it. The signal existed and was
wired to a log line.

This module consumes it and can say no.

WHY PROVENANCE IS THE FIRST CHECK. `origin` (LIVE / REPLAY /
HISTORICAL_RECONSTRUCTION / SYNTHETIC) was stamped on every observation and
had exactly two readers in the whole codebase: a membership check against the
taxonomy, and a re-serialization. Nothing branched on it, so nothing
structurally prevented synthetic or replayed data from reaching a live
decision. Provenance was documentation. Here it is a precondition.

FAIL-CLOSED, ON PURPOSE. An unrecognised health status, an absent snapshot,
a snapshot whose provenance cannot be determined -- all INVALID. The entire
value of this gate is that it refuses when it does not know, and a gate that
defaults to permitting on the paths its author did not anticipate is not a
safety boundary. This is the opposite of the rate budget's deliberate
fail-open: a rate ceiling protects throughput, and refusing to trade because
a lock file is unwritable turns a throughput problem into an outage. Data
quality protects capital, and the safe direction is the other way.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Sequence, Tuple

QUALITY_GOOD = "GOOD"
QUALITY_DEGRADED = "DEGRADED"
QUALITY_INVALID = "INVALID"
QUALITY_UNAVAILABLE = "UNAVAILABLE"
ALL_QUALITY_STATES = (QUALITY_GOOD, QUALITY_DEGRADED, QUALITY_INVALID, QUALITY_UNAVAILABLE)

# Absent, these cannot support constructing an options trade at all: there is
# no underlying to reason about and no chain to build legs from. Their absence
# is a hard stop, not a degradation.
LOAD_BEARING = ("spot", "option_chain")

REASON_NO_SNAPSHOT = "NO_SNAPSHOT"
REASON_SPOT_UNAVAILABLE = "SPOT_UNAVAILABLE"
REASON_UNKNOWN_HEALTH = "UNRECOGNISED_HEALTH_STATUS"
REASON_NON_LIVE_ORIGIN = "NON_LIVE_ORIGIN"
REASON_EVIDENCE_TRAIL_BROKEN = "EVIDENCE_TRAIL_BROKEN"
REASON_REQUIRED_FIELD_UNAVAILABLE = "REQUIRED_FIELD_UNAVAILABLE"
REASON_REQUIRED_FIELD_STALE = "REQUIRED_FIELD_STALE"
REASON_DISALLOWED_PROVENANCE = "DISALLOWED_FIELD_PROVENANCE"
REASON_QUOTE_MALFORMED = "QUOTE_MALFORMED"

# WHICH FIELDS A DECISION NEEDS IS THE CALLER'S TO STATE, not this module's.
#
# The plumbing here is deliberately GENERIC. A stop-loss needs a price; a
# spread-sensitive entry needs both book sides; a liquidity filter needs
# volume or open interest. Hardcoding one policy would make every decision
# share the strictest requirement, and choosing that policy before Monday's
# Gate 1 measurement would mean choosing it from assumptions about which
# FYERS fields arrive at all.
#
# So: callers pass what they need, this refuses when they cannot have it, and
# the FYERS-specific policy is set after there is evidence to set it from.


@dataclass(frozen=True)
class DataQualityVerdict:
    quality: str
    may_trade: bool
    reasons: Tuple[str, ...]
    missing_fields: Tuple[str, ...] = ()
    origin: Optional[str] = None
    latency_ms: Optional[float] = None
    evidence_resolution_rate: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "quality": self.quality,
            "may_trade": self.may_trade,
            "reasons": list(self.reasons),
            "missing_fields": list(self.missing_fields),
            "origin": self.origin,
            "latency_ms": self.latency_ms,
            "evidence_resolution_rate": self.evidence_resolution_rate,
        }


def assess_market_data(
    snapshot: Any,
    *,
    evidence_integrity: Optional[Dict[str, Any]] = None,
    required_origin: str = "LIVE",
    require_whole_evidence_trail: bool = False,
) -> DataQualityVerdict:
    """Decide whether this snapshot may support a trading decision.

    `require_whole_evidence_trail` defaults False and is the one deliberately
    NOT-yet-blocking condition. A broken trail does not make a decision wrong,
    it makes it unauditable -- and promoting it to a hard stop before a single
    live session has measured the real resolution rate risks a fail-closed
    gate that silently prevents all trading. It is reported on every verdict
    and logged loudly by the caller; the promotion criterion is one full live
    session at a measured 100% resolution rate. Measure, then enforce.
    """
    reasons = []

    if snapshot is None:
        return DataQualityVerdict(
            quality=QUALITY_INVALID, may_trade=False, reasons=(REASON_NO_SNAPSHOT,))

    health = getattr(snapshot, "health_status", None)
    missing = tuple(getattr(snapshot, "missing_fields", ()) or ())
    latency = getattr(snapshot, "latency_ms", None)
    origin = _origin_of(snapshot)
    rate = (evidence_integrity or {}).get("resolution_rate")

    def verdict(quality: str, may_trade: bool) -> DataQualityVerdict:
        return DataQualityVerdict(
            quality=quality, may_trade=may_trade, reasons=tuple(reasons),
            missing_fields=missing, origin=origin, latency_ms=latency,
            evidence_resolution_rate=rate)

    # 1. PROVENANCE FIRST. Synthetic or replayed data must never reach a
    #    production decision, however healthy it looks -- and it looks
    #    perfectly healthy, which is the point.
    if required_origin and origin is not None and origin != required_origin:
        reasons.append(f"{REASON_NON_LIVE_ORIGIN}:{origin}")
        return verdict(QUALITY_INVALID, False)

    # 2. LOAD-BEARING DATA. No underlying or no chain means no options trade
    #    can be constructed at all.
    blocking_absences = [f for f in LOAD_BEARING if f in missing]
    if health == "UNAVAILABLE" or "spot" in blocking_absences:
        reasons.append(REASON_SPOT_UNAVAILABLE if "spot" in blocking_absences
                       else "HEALTH_UNAVAILABLE")
        for field_name in blocking_absences:
            reasons.append(f"MISSING:{field_name}")
        return verdict(QUALITY_UNAVAILABLE, False)
    if blocking_absences:
        for field_name in blocking_absences:
            reasons.append(f"MISSING:{field_name}")
        return verdict(QUALITY_INVALID, False)

    # 3. FAIL CLOSED on a status this gate does not recognise. A gate that
    #    permits on the paths its author did not anticipate is not a gate.
    if health not in ("OK", "DEGRADED"):
        reasons.append(f"{REASON_UNKNOWN_HEALTH}:{health!r}")
        return verdict(QUALITY_INVALID, False)

    # 4. EVIDENCE TRAIL. Reported always; blocking only when required.
    #
    # When not enforced it still DEGRADES the verdict rather than leaving it
    # GOOD. A verdict reading GOOD while carrying EVIDENCE_TRAIL_BROKEN is
    # precisely the false-reassurance shape this whole gate exists to remove
    # -- a dashboard would show green over decisions that cannot be
    # reconstructed.
    trail_broken = (evidence_integrity is not None
                    and evidence_integrity.get("all_cited_ids_resolve") is False)
    if trail_broken:
        reasons.append(REASON_EVIDENCE_TRAIL_BROKEN)
        if require_whole_evidence_trail:
            return verdict(QUALITY_INVALID, False)

    # 5. NON-LOAD-BEARING absences degrade rather than stop. VIX and futures
    #    inform lenses that already go honestly dark without them -- the
    #    absence discipline handles this correctly, so removing the trade
    #    entirely would be stricter than the evidence warrants.
    if missing or health == "DEGRADED" or trail_broken:
        for field_name in missing:
            reasons.append(f"DEGRADED_MISSING:{field_name}")
        return verdict(QUALITY_DEGRADED, True)

    return verdict(QUALITY_GOOD, True)


def assess_quote_fields(
    quotes: Dict[str, Any],
    required_fields: Sequence[str],
    *,
    now_mono: float,
    max_age_seconds: float,
    allowed_sources: Sequence[str] = ("LIVE_TICK",),
    required_symbols: Optional[Sequence[str]] = None,
) -> DataQualityVerdict:
    """Can a decision that needs THESE fields be made from THESE quotes?

    Three independent ways to be refused, and they are distinguished because
    they call for different operator actions:

      UNAVAILABLE  the source never supplied the field. Waiting will not help;
                   either the field does not arrive on this feed or the
                   projection does not yet map it.
      STALE        it arrived, and is too old. The feed may have gone quiet.
      DISALLOWED   it is present and fresh, but came from a source this
                   decision does not accept -- a REST price standing in for
                   book evidence, or replayed data reaching a live decision.

    FAIL CLOSED ON THE UNKNOWN. A symbol with no quote at all, a quote whose
    age cannot be established, an unrecognised source: all refuse. A gate that
    permits on the paths its author did not anticipate is not a gate.
    """
    reasons: list = []
    missing: list = []
    symbols = list(required_symbols if required_symbols is not None else quotes.keys())

    if not symbols:
        return DataQualityVerdict(
            quality=QUALITY_UNAVAILABLE, may_trade=False,
            reasons=(REASON_NO_SNAPSHOT,),
            missing_fields=tuple(required_fields))

    for symbol in symbols:
        q = quotes.get(symbol)
        if q is None:
            reasons.append(f"{REASON_REQUIRED_FIELD_UNAVAILABLE}:{symbol}:<no quote>")
            missing.append(f"{symbol}:*")
            continue

        # Age from the quote's own monotonic stamp. Unknown age is refused,
        # never treated as fresh.
        age = None
        try:
            age = q.age_seconds(now_mono)
        except Exception:  # noqa: BLE001
            age = None
        if age is None:
            reasons.append(f"{REASON_QUOTE_MALFORMED}:{symbol}:<no monotonic stamp>")
            missing.append(f"{symbol}:*")
            continue

        for name in required_fields:
            try:
                fv = q.get(name)
            except Exception:  # noqa: BLE001
                reasons.append(f"{REASON_QUOTE_MALFORMED}:{symbol}:{name}")
                missing.append(f"{symbol}:{name}")
                continue
            if not fv.is_available:
                reasons.append(f"{REASON_REQUIRED_FIELD_UNAVAILABLE}:{symbol}:{name}")
                missing.append(f"{symbol}:{name}")
                continue
            if fv.source not in allowed_sources:
                reasons.append(f"{REASON_DISALLOWED_PROVENANCE}:{symbol}:{name}"
                               f":{fv.source}")
                missing.append(f"{symbol}:{name}")
                continue
            field_age = fv.age_seconds(now_mono)
            if field_age is None or field_age > max_age_seconds:
                reasons.append(f"{REASON_REQUIRED_FIELD_STALE}:{symbol}:{name}"
                               f":{'unknown' if field_age is None else round(field_age, 1)}s")
                missing.append(f"{symbol}:{name}")

    if reasons:
        return DataQualityVerdict(
            quality=QUALITY_INVALID, may_trade=False,
            reasons=tuple(reasons[:40]), missing_fields=tuple(sorted(set(missing))))

    sources = sorted({q.get(f).source for q in
                      (quotes[s] for s in symbols if quotes.get(s))
                      for f in required_fields})
    return DataQualityVerdict(
        quality=QUALITY_GOOD, may_trade=True,
        reasons=(f"all required fields available, fresh, and from "
                 f"{sources or ['<none>']}",),
        missing_fields=(), origin=",".join(sources) or None)


def _origin_of(snapshot: Any) -> Optional[str]:
    """The provenance of the data behind this snapshot.

    Read from the snapshot's own source marker. Returns None when it cannot
    be determined -- and None is NOT treated as a violation above, because a
    snapshot type that never carried provenance is a wiring gap, not evidence
    of synthetic data. That gap is closed by giving the snapshot an origin,
    not by refusing every caller that predates it.
    """
    origin = getattr(snapshot, "origin", None)
    if origin:
        return str(origin)
    source = getattr(snapshot, "source", None)
    if not source:
        return None
    lowered = str(source).lower()
    # Order matters: a source naming BOTH a live broker and a replay wrapper
    # (e.g. "fyers_live_replay") is a replay. The narrower, more dangerous
    # classification wins, never the reassuring one.
    if "replay" in lowered:
        return "REPLAY"
    if "synthetic" in lowered or "paper" in lowered or "random" in lowered:
        return "SYNTHETIC"
    if "historical" in lowered or "reconstruct" in lowered:
        return "HISTORICAL_RECONSTRUCTION"
    if "live" in lowered:
        return "LIVE"
    return None
