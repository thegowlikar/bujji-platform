"""Strict offline verification of OI decision evidence, and the not-yet-active
intelligence-to-decision contract.

REFUSES BY DEFAULT. Every check here answers "can this decision be replayed
against the evidence it claims?" and the answer is no unless the evidence says
otherwise. An absent reference, an ambiguous one, or a value whose provenance
cannot be established is a refusal -- never a warning, never a default.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

# Refusal codes. One code per distinguishable defect, so "why was this
# unreplayable?" is answerable from the record rather than from prose.
MISSING_SNAPSHOT_REFERENCE = "MISSING_SNAPSHOT_REFERENCE"
AMBIGUOUS_LINKAGE = "AMBIGUOUS_LINKAGE"
CONTRACT_IDENTITY_MISMATCH = "CONTRACT_IDENTITY_MISMATCH"
EXPIRY_IDENTITY_MISMATCH = "EXPIRY_IDENTITY_MISMATCH"
MALFORMED_VALUE = "MALFORMED_VALUE"
UNAVAILABLE_PRESENTED_AS_VALUE = "UNAVAILABLE_PRESENTED_AS_VALUE"
UNKNOWN_PROVENANCE = "UNKNOWN_PROVENANCE"
TICK_PROVENANCE_ON_REST_FACT = "TICK_PROVENANCE_ON_REST_FACT"

# The only provenance an OI fact may carry. OI never arrives on a tick.
REST_PROVENANCE = "REST_CHAIN_SNAPSHOT"
_TICKISH = ("LIVE_TICK", "SOURCE_TICK", "TICK", "WEBSOCKET", "REST_FALLBACK")


@dataclass
class Verdict:
    refusals: List[Dict[str, Any]] = field(default_factory=list)
    checked: int = 0

    @property
    def replayable(self) -> bool:
        return not self.refusals

    def refuse(self, code: str, detail: str, subject: Any = None) -> None:
        self.refusals.append({"code": code, "detail": detail, "subject": subject})

    def as_dict(self) -> Dict[str, Any]:
        return {"replayable": self.replayable, "checked": self.checked,
                "refusals": self.refusals}


def verify_oi_evidence(records: Sequence[Dict[str, Any]],
                       *, expected_expiry: Optional[str] = None) -> Verdict:
    """Verify OI decision-evidence records against the replay contract.

    `records` are the `oi_evidence` mappings attached to the strike evidence a
    decision used -- the identity of the OptionObservation each number came
    from, not a copy of the observation.
    """
    v = Verdict()
    seen_ids: Dict[str, Any] = {}

    for rec in records:
        v.checked += 1
        if not isinstance(rec, dict) or not rec:
            v.refuse(MISSING_SNAPSHOT_REFERENCE,
                     "no OI evidence mapping was recorded for this leg", rec)
            continue

        oid = rec.get("observation_id")
        ts = rec.get("chain_timestamp")
        prov = rec.get("provenance")
        avail = rec.get("availability")
        oi = rec.get("open_interest")
        sym = rec.get("instrument_symbol")

        if not oid:
            v.refuse(MISSING_SNAPSHOT_REFERENCE,
                     "no observation_id: the decision cannot name the contract "
                     "record it used", sym)
        if not ts:
            v.refuse(MISSING_SNAPSHOT_REFERENCE,
                     "no chain_timestamp: the age of the OI behind this "
                     "decision is unknown", sym or oid)

        # provenance -------------------------------------------------------
        if not prov:
            v.refuse(UNKNOWN_PROVENANCE,
                     "no provenance recorded; an OI whose source is unknown "
                     "cannot be replayed", sym or oid)
        elif str(prov).upper() in _TICKISH:
            v.refuse(TICK_PROVENANCE_ON_REST_FACT,
                     f"provenance {prov!r} is a TICK provenance. OI is a "
                     f"REST-chain fact and the websocket carries none; a tick "
                     f"provenance here is a false claim of simultaneity",
                     sym or oid)
        elif str(prov) != REST_PROVENANCE:
            v.refuse(UNKNOWN_PROVENANCE,
                     f"provenance {prov!r} is not {REST_PROVENANCE}", sym or oid)

        # availability vs value -------------------------------------------
        if avail not in ("AVAILABLE", "UNAVAILABLE"):
            v.refuse(MALFORMED_VALUE,
                     f"availability {avail!r} is neither AVAILABLE nor "
                     f"UNAVAILABLE", sym or oid)
        elif avail == "UNAVAILABLE" and oi is not None:
            v.refuse(UNAVAILABLE_PRESENTED_AS_VALUE,
                     f"availability says UNAVAILABLE but a value {oi!r} is "
                     f"present; one of the two is a fabrication", sym or oid)
        elif avail == "AVAILABLE" and oi is None:
            v.refuse(MALFORMED_VALUE,
                     "availability says AVAILABLE but no value is present",
                     sym or oid)

        for name in ("open_interest", "previous_open_interest",
                     "change_in_open_interest"):
            val = rec.get(name)
            if val is not None and not isinstance(val, (int, float)):
                v.refuse(MALFORMED_VALUE,
                         f"{name}={val!r} is not numeric", sym or oid)
            if isinstance(val, bool):
                v.refuse(MALFORMED_VALUE,
                         f"{name} is a bool, not a measurement", sym or oid)

        # linkage ambiguity -------------------------------------------------
        if oid:
            prior = seen_ids.get(oid)
            if prior is not None and prior != sym:
                v.refuse(AMBIGUOUS_LINKAGE,
                         f"observation_id {oid} is claimed by two different "
                         f"contracts ({prior!r} and {sym!r})", oid)
            seen_ids[oid] = sym

        if expected_expiry is not None:
            exp = rec.get("expiry")
            if exp is not None and exp != expected_expiry:
                v.refuse(EXPIRY_IDENTITY_MISMATCH,
                         f"evidence expiry {exp!r} is not the decision's "
                         f"expiry {expected_expiry!r}", sym or oid)

        want_sym = rec.get("expected_symbol")
        if want_sym is not None and sym is not None and want_sym != sym:
            v.refuse(CONTRACT_IDENTITY_MISMATCH,
                     f"evidence is for {sym!r} but the decision used "
                     f"{want_sym!r}", oid)
    return v


# ---------------------------------------------------------------------------
# E. The intelligence-to-decision contract -- CONSTRUCTED, NOT CONSUMED.
# ---------------------------------------------------------------------------
def decision_market_context(oi_records: Sequence[Dict[str, Any]],
                            *, report: Optional[Any] = None) -> Dict[str, Any]:
    """The record a later policy would read, built now and consumed by nothing.

    The point is that a future decision can say: *these named market facts,
    from these timestamps and sources, with these fields unavailable* -- rather
    than silently consuming a float, a fabricated zero, or a stale snapshot.

    NOT WIRED, DELIBERATELY. Nothing in selection, ranking, sizing, risk or
    execution reads this. Activating it is a separate, explicit decision that
    requires measured evidence about freshness and field availability that
    does not exist yet. A test asserts no strategy, risk or execution module
    imports this function.
    """
    verdict = verify_oi_evidence(oi_records)
    available = [r for r in oi_records
                 if isinstance(r, dict) and r.get("availability") == "AVAILABLE"]
    unavailable = [r for r in oi_records
                   if isinstance(r, dict) and r.get("availability") != "AVAILABLE"]
    stamps = sorted({r.get("chain_timestamp") for r in oi_records
                     if isinstance(r, dict) and r.get("chain_timestamp")})
    return {
        "contract": "market_intelligence.decision_market_context/1",
        "status": "CONSTRUCTED_NOT_CONSUMED",
        "facts_considered": [
            {"symbol": r.get("instrument_symbol"),
             "observation_id": r.get("observation_id"),
             "chain_timestamp": r.get("chain_timestamp"),
             "provenance": r.get("provenance"),
             "open_interest": r.get("open_interest"),
             "previous_open_interest": r.get("previous_open_interest"),
             "change_in_open_interest": r.get("change_in_open_interest")}
            for r in available
        ],
        "fields_unavailable": [
            {"symbol": r.get("instrument_symbol"),
             "observation_id": r.get("observation_id"),
             "missing_fields": list(r.get("missing_fields") or ()),
             "reason": "OI not supplied by the chain snapshot"}
            for r in unavailable
        ],
        "sources": [REST_PROVENANCE],
        "chain_timestamps": stamps,
        "evidence_replayable": verdict.replayable,
        "evidence_refusals": verdict.refusals,
        "descriptive_report": (report.as_dict() if report is not None
                               and hasattr(report, "as_dict") else None),
        "authorizes": ("NOTHING. This record describes what was observed. It "
                       "is not a signal, not a ranking input, and not a "
                       "permission to trade."),
    }
