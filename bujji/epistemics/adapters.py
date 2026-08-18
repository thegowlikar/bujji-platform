"""Adapters from the six existing confidence vocabularies onto the
canonical `Uncertainty` model -- Phase 16C.

THE POINT OF THIS FILE: no package is rewritten. Each subsystem keeps
its own vocabulary internally and exposes `Uncertainty` at its
boundary. That is the difference between CONSOLIDATION and a
disruptive migration, and it is why adopting the canonical model costs
existing code nothing.

Audited vocabularies (each verified in-repo, not assumed):

  CONFIDENCE_{HIGH,MODERATE,LOW,NONE}   msi_* family (17+ packages)
  STATUS_{KNOWN,PARTIAL,UNKNOWN}        portfolio_intelligence (15M)
  PNL_{COMPLETE,PARTIAL,UNKNOWN}        position_lifecycle (15K)
  QUALITY_{EXCELLENT,GOOD,FAIR,POOR}    msi_decision_synthesis
  STRENGTH_{STRONG,MODERATE,WEAK,UNKNOWN}  outcome_attribution (15J)
  {KNOWN,UNKNOWN,NOT_APPLICABLE,NOT_AVAILABLE}  outcome_memory (15N)

15N's 4-state vocabulary is the closest existing match and maps
one-to-one -- it was already the right idea, just not shared.
"""
from __future__ import annotations

from typing import Optional

from .uncertainty import (
    DEGRADED, HIGH, KNOWN, LOW, MODERATE, NONE, NOT_APPLICABLE, NOT_AVAILABLE,
    UNKNOWN, Uncertainty,
)

# --- msi_* CONFIDENCE_* ------------------------------------------------------
_MSI_CONFIDENCE = {"HIGH": HIGH, "MODERATE": MODERATE, "MEDIUM": MODERATE,
                   "LOW": LOW, "NONE": NONE, "UNKNOWN": NONE}


def from_msi_confidence(value: Optional[str], *, source: str = "") -> Uncertainty:
    """`CONFIDENCE_HIGH/MODERATE/LOW/NONE` (+ one stray `MEDIUM`)."""
    if value is None or value in ("UNKNOWN", "NONE"):
        return Uncertainty(state=UNKNOWN, confidence=NONE,
                           limiting_factor=f"{source}:confidence={value}" if source else None,
                           provenance=(source,) if source else ())
    return Uncertainty(state=KNOWN, confidence=_MSI_CONFIDENCE.get(value, NONE),
                       provenance=(source,) if source else ())


# --- portfolio_intelligence STATUS_* (15M) -----------------------------------
def from_portfolio_status(value: Optional[str], *, source: str = "") -> Uncertainty:
    """`KNOWN` / `PARTIAL` / `UNKNOWN`. PARTIAL means some contributing
    positions resolved and some did not -- that is DEGRADED, and 15M
    already refuses to emit a total in that case."""
    if value == "KNOWN":
        return Uncertainty(state=KNOWN, confidence=HIGH, provenance=(source,) if source else ())
    if value == "PARTIAL":
        return Uncertainty(state=DEGRADED, confidence=LOW,
                           limiting_factor=f"{source}:PARTIAL" if source else "PARTIAL",
                           provenance=(source,) if source else ())
    return Uncertainty(state=UNKNOWN, confidence=NONE,
                       limiting_factor=f"{source}:UNKNOWN" if source else "UNKNOWN",
                       provenance=(source,) if source else ())


# --- position_lifecycle PNL_* (15K) ------------------------------------------
def from_pnl_status(value: Optional[str], *, source: str = "") -> Uncertainty:
    """`COMPLETE` / `PARTIAL` / `UNKNOWN`. 15K already refuses to emit a
    total when only some legs resolved -- PARTIAL is DEGRADED, and the
    absence of a number is itself the correct behaviour."""
    if value == "COMPLETE":
        return Uncertainty(state=KNOWN, confidence=HIGH, provenance=(source,) if source else ())
    if value == "PARTIAL":
        return Uncertainty(state=DEGRADED, confidence=LOW,
                           limiting_factor=f"{source}:PNL_PARTIAL" if source else "PNL_PARTIAL",
                           provenance=(source,) if source else ())
    return Uncertainty(state=UNKNOWN, confidence=NONE,
                       limiting_factor=f"{source}:PNL_UNKNOWN" if source else "PNL_UNKNOWN",
                       provenance=(source,) if source else ())


# --- msi_decision_synthesis QUALITY_* ----------------------------------------
_QUALITY = {"EXCELLENT": HIGH, "GOOD": MODERATE, "FAIR": LOW, "POOR": NONE}


def from_quality(value: Optional[str], *, source: str = "") -> Uncertainty:
    """Opportunity QUALITY is a coverage x self-reported-confidence
    score, deliberately independent of agreement/conflict. It maps to a
    confidence band, never to a state."""
    band = _QUALITY.get(value or "", NONE)
    state = KNOWN if band != NONE else UNKNOWN
    return Uncertainty(state=state, confidence=band,
                       limiting_factor=f"{source}:quality={value}" if band != HIGH and source else None,
                       provenance=(source,) if source else ())


# --- outcome_attribution STRENGTH_* (15J) ------------------------------------
_STRENGTH = {"STRONG": HIGH, "MODERATE": MODERATE, "WEAK": LOW, "UNKNOWN": NONE}


def from_evidence_strength(value: Optional[str], *, source: str = "") -> Uncertainty:
    band = _STRENGTH.get(value or "", NONE)
    return Uncertainty(state=KNOWN if band != NONE else UNKNOWN, confidence=band,
                       limiting_factor=f"{source}:strength={value}" if band != HIGH and source else None,
                       provenance=(source,) if source else ())


# --- outcome_memory epistemic 4-state (15N) ----------------------------------
_MEMORY_STATE = {"KNOWN": KNOWN, "UNKNOWN": UNKNOWN,
                 "NOT_APPLICABLE": NOT_APPLICABLE, "NOT_AVAILABLE": NOT_AVAILABLE}


def from_memory_epistemic(value: Optional[str], *, source: str = "") -> Uncertainty:
    """15N's vocabulary was already the right idea -- a one-to-one map.
    NOT_APPLICABLE deliberately carries NONE confidence but must not
    degrade siblings; `compose()` ignores it entirely."""
    state = _MEMORY_STATE.get(value or "", UNKNOWN)
    return Uncertainty(state=state, confidence=HIGH if state == KNOWN else NONE,
                       limiting_factor=None if state in (KNOWN, NOT_APPLICABLE)
                       else f"{source}:{state}" if source else state,
                       provenance=(source,) if source else ())


# --- execution_reality DATA_QUALITY_* ----------------------------------------
def from_data_quality(value: Optional[str], *, source: str = "") -> Uncertainty:
    """Quote-level data quality. `UNAVAILABLE` is genuine absence of a
    quote, not a zero -- the distinction Phase 14 onwards depends on."""
    if value in (None, "UNAVAILABLE", "DATA_QUALITY_UNAVAILABLE"):
        return Uncertainty(state=UNKNOWN, confidence=NONE,
                           limiting_factor=f"{source}:quote_unavailable" if source else "quote_unavailable",
                           provenance=(source,) if source else ())
    return Uncertainty(state=KNOWN, confidence=HIGH, provenance=(source,) if source else ())


ADAPTERS = {
    "msi_confidence": from_msi_confidence,
    "portfolio_status": from_portfolio_status,
    "pnl_status": from_pnl_status,
    "quality": from_quality,
    "evidence_strength": from_evidence_strength,
    "memory_epistemic": from_memory_epistemic,
    "data_quality": from_data_quality,
}
