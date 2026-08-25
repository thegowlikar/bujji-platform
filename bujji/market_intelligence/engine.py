"""Descriptive market intelligence over persisted option-chain observations.

OFFLINE AND EVIDENCE-ONLY. Nothing here may be reached from a trading
entrypoint. It computes and reports; it never selects, ranks, sizes, gates or
executes. `tools/reachability.py` is the enforcement: a test asserts this
module is absent from the closure of every declared entrypoint, because in
this codebase "offline" written in a docstring has repeatedly meant "reachable
and simply not called yet".

WHAT IT READS. `OptionObservation` records -- the existing snapshot-fact
authority, carrying oi / prev_oi / oich, contract identity, chain timestamp
and `missing_fields`. No second OI model is defined here and none may be.

WHAT IT CANNOT SAY, AND WHY. Open interest is the count of contracts
outstanding, aggregated and unsigned. It says nothing about who opened them or
why. Bujji has no signed transaction flow and no participant data, so the
familiar readings -- "long buildup", "short covering", "smart money
positioning" -- are conventions, not inferences. A rise in OI with a rise in
price cannot distinguish a buyer opening from a seller opening: both create
exactly one contract. Every quadrant this module emits is therefore labelled
heuristic descriptive context and names no direction.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

# Availability is a fact about the DATA, never a value substituted for it.
AVAILABLE = "AVAILABLE"
UNAVAILABLE = "UNAVAILABLE"

# The quadrant vocabulary is deliberately non-directional. There is no
# "bullish" or "bearish" member, and none may be added without signed flow.
QUADRANT_PRICE_UP_OI_UP = "PRICE_UP_OI_UP"
QUADRANT_PRICE_UP_OI_DOWN = "PRICE_UP_OI_DOWN"
QUADRANT_PRICE_DOWN_OI_UP = "PRICE_DOWN_OI_UP"
QUADRANT_PRICE_DOWN_OI_DOWN = "PRICE_DOWN_OI_DOWN"
QUADRANT_INDETERMINATE = "INDETERMINATE"

NON_ACTIONABLE = (
    "DESCRIPTIVE ONLY. This report contains no recommendation, no signal, no "
    "ranking and no directional inference. Open interest is unsigned and "
    "aggregated: it cannot identify buyer or seller initiation, participant "
    "class, opening versus closing flow, or bullish/bearish intent. Any such "
    "reading would require signed transaction flow and participant data, "
    "neither of which Bujji has."
)


@dataclass(frozen=True)
class ContractOI:
    """One contract's OI facts, carried with their provenance."""
    symbol: Optional[str]
    strike: Optional[float]
    option_type: Optional[str]
    expiry: Optional[str]
    observation_id: Optional[str]
    chain_timestamp: Optional[str]
    open_interest: Optional[float]
    previous_open_interest: Optional[float]
    change_in_open_interest: Optional[float]
    availability: str
    missing_fields: Tuple[str, ...] = ()

    @property
    def has_oi(self) -> bool:
        return self.open_interest is not None


@dataclass
class OIReport:
    """A typed descriptive report. Never an instruction."""
    contracts: List[ContractOI] = field(default_factory=list)
    coverage: Dict[str, Any] = field(default_factory=dict)
    by_expiry: Dict[str, Any] = field(default_factory=dict)
    concentration: Dict[str, Any] = field(default_factory=dict)
    put_call: Dict[str, Any] = field(default_factory=dict)
    oich_stats: Dict[str, Any] = field(default_factory=dict)
    quadrants: Dict[str, Any] = field(default_factory=dict)
    data_quality: Dict[str, Any] = field(default_factory=dict)
    limits: str = NON_ACTIONABLE

    def as_dict(self) -> Dict[str, Any]:
        return {
            "coverage": self.coverage, "by_expiry": self.by_expiry,
            "concentration": self.concentration, "put_call": self.put_call,
            "oich_stats": self.oich_stats, "quadrants": self.quadrants,
            "data_quality": self.data_quality, "limits": self.limits,
            "contract_count": len(self.contracts),
        }


def to_contract_oi(obs) -> ContractOI:
    """Project one OptionObservation into its OI facts, losing nothing.

    Absence stays absence. There is no `or 0.0` anywhere in this function and
    none may be added: a fabricated zero here would propagate into every ratio
    and concentration figure below, silently, and look like measurement.
    """
    oi = getattr(obs, "open_interest", None)
    return ContractOI(
        symbol=getattr(obs, "instrument_symbol", None),
        strike=_meta(obs, "strike"),
        option_type=_meta(obs, "option_type"),
        expiry=_meta(obs, "expiry"),
        observation_id=getattr(obs, "observation_id", None),
        chain_timestamp=getattr(obs, "timestamp", None),
        open_interest=oi,
        previous_open_interest=getattr(obs, "previous_open_interest", None),
        change_in_open_interest=getattr(obs, "change_in_open_interest", None),
        availability=AVAILABLE if oi is not None else UNAVAILABLE,
        missing_fields=tuple(getattr(obs, "missing_fields", ()) or ()),
    )


def _meta(obs, name):
    """Contract identity, wherever the observation happens to carry it."""
    for holder in (obs, getattr(obs, "instrument", None),
                   getattr(obs, "contract", None)):
        if holder is None:
            continue
        v = getattr(holder, name, None)
        if v is not None:
            return v
    return None


def build_report(observations: Sequence, spot: Optional[float] = None) -> OIReport:
    """Describe what the chain snapshot shows. Decide nothing."""
    contracts = [to_contract_oi(o) for o in observations]
    rep = OIReport(contracts=contracts)
    total = len(contracts)
    with_oi = [c for c in contracts if c.has_oi]

    # ---- coverage: missingness is reported, never imputed ---------------
    rep.coverage = {
        "contracts": total,
        "oi_available": len(with_oi),
        "oi_unavailable": total - len(with_oi),
        "oi_availability_pct": (round(100.0 * len(with_oi) / total, 2)
                                if total else None),
        "prev_oi_available": sum(1 for c in contracts
                                 if c.previous_open_interest is not None),
        "oich_available": sum(1 for c in contracts
                              if c.change_in_open_interest is not None),
    }

    # ---- put/call, with the denominator's honesty attached --------------
    ce = [c for c in with_oi if c.option_type == "CE"]
    pe = [c for c in with_oi if c.option_type == "PE"]
    ce_oi = sum(c.open_interest for c in ce)
    pe_oi = sum(c.open_interest for c in pe)
    rep.put_call = {
        "ce_contracts_counted": len(ce), "pe_contracts_counted": len(pe),
        "ce_oi_total": ce_oi, "pe_oi_total": pe_oi,
        "pcr_oi": (round(pe_oi / ce_oi, 4) if ce_oi else None),
        "pcr_basis": ("computed over contracts WITH available OI only; "
                      f"{total - len(with_oi)} of {total} contracts were "
                      f"excluded because their OI is unavailable"),
        "interpretation_limit": ("A ratio is a description of the book, not a "
                                 "view. It carries no direction."),
    }

    # ---- concentration and OI-weighted distance -------------------------
    by_strike: Dict[float, float] = defaultdict(float)
    for c in with_oi:
        if c.strike is not None:
            by_strike[float(c.strike)] += c.open_interest
    ranked = sorted(by_strike.items(), key=lambda kv: kv[1], reverse=True)
    grand = sum(by_strike.values())
    rep.concentration = {
        "strikes_with_oi": len(by_strike),
        "top_strikes": [{"strike": k, "oi": v,
                         "share_pct": (round(100.0 * v / grand, 2) if grand else None)}
                        for k, v in ranked[:10]],
        "herfindahl": (round(sum((v / grand) ** 2 for v in by_strike.values()), 6)
                       if grand else None),
        "note": ("A concentration peak is where contracts are outstanding. It "
                 "is not a support level, a target, or a prediction."),
    }
    if spot is not None and grand:
        weighted = sum(abs(float(k) - spot) * v for k, v in by_strike.items()) / grand
        rep.concentration["oi_weighted_distance_from_spot"] = round(weighted, 2)
        rep.concentration["spot_used"] = spot
    else:
        rep.concentration["oi_weighted_distance_from_spot"] = None
        rep.concentration["spot_used"] = spot

    # ---- per-expiry, so an expiry roll is visible -----------------------
    per_expiry: Dict[str, Dict[str, Any]] = {}
    for c in contracts:
        k = c.expiry or "UNKNOWN_EXPIRY"
        e = per_expiry.setdefault(k, {"contracts": 0, "oi_available": 0,
                                      "oi_total": 0.0, "oich_total": 0.0,
                                      "oich_available": 0})
        e["contracts"] += 1
        if c.has_oi:
            e["oi_available"] += 1
            e["oi_total"] += c.open_interest
        if c.change_in_open_interest is not None:
            e["oich_available"] += 1
            e["oich_total"] += c.change_in_open_interest
    rep.by_expiry = per_expiry

    # ---- broker oich only. No competing delta is computed here ----------
    oich = [c.change_in_open_interest for c in contracts
            if c.change_in_open_interest is not None]
    rep.oich_stats = {
        "source": "BROKER_REPORTED_OICH",
        "n": len(oich),
        "sum": sum(oich) if oich else None,
        "mean": (round(sum(oich) / len(oich), 2) if oich else None),
        "min": min(oich) if oich else None,
        "max": max(oich) if oich else None,
        "dispersion_range": (max(oich) - min(oich)) if oich else None,
        "note": ("The broker's own oich is used verbatim. Bujji does not "
                 "compute a competing oi - prev_oi delta: two answers to one "
                 "question is how a split starts."),
    }

    rep.quadrants = {
        "computed": False,
        "reason": ("A price/OI quadrant needs a price change over the SAME "
                   "interval as the OI change. A single chain snapshot carries "
                   "oich but no matching price delta, so no quadrant is "
                   "emitted rather than one assembled from mismatched "
                   "intervals."),
        "vocabulary": [QUADRANT_PRICE_UP_OI_UP, QUADRANT_PRICE_UP_OI_DOWN,
                       QUADRANT_PRICE_DOWN_OI_UP, QUADRANT_PRICE_DOWN_OI_DOWN,
                       QUADRANT_INDETERMINATE],
        "vocabulary_note": ("Deliberately non-directional. No member names a "
                            "bullish or bearish reading, because OI alone "
                            "cannot support one."),
    }

    stamps = sorted({c.chain_timestamp for c in contracts if c.chain_timestamp})
    rep.data_quality = {
        "provenance": "REST_CHAIN_SNAPSHOT",
        "distinct_chain_timestamps": len(stamps),
        "earliest": stamps[0] if stamps else None,
        "latest": stamps[-1] if stamps else None,
        "snapshot_continuity": ("SINGLE_SNAPSHOT" if len(stamps) == 1
                                else "MULTIPLE_SNAPSHOTS" if stamps
                                else "NO_TIMESTAMPS"),
        "contracts_without_identity": sum(
            1 for c in contracts if not c.observation_id),
        "confidence_limit": ("Coverage and freshness are reported; predictive "
                             "confidence is not claimed and is not computable "
                             "from this data."),
        "tick_provenance_present": False,
        "tick_provenance_note": ("OI is a REST-chain fact. No tick timestamp "
                                 "or tick provenance appears in this report, "
                                 "and the two must never be presented as one "
                                 "simultaneous observation."),
    }
    return rep
