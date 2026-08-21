"""Canonical uncertainty model -- Phase 16C.

Pure semantics. No IO, no broker, no market data, no execution. Nothing
here depends on Gate 1 measurements.

WHY THIS EXISTS (audited, not assumed): 20 packages define their own
confidence constants across at least six parallel scales
(`CONFIDENCE_*`, `STATUS_*`, `PNL_*`, `QUALITY_*`, `STRENGTH_*`, and
15N's own 4-state epistemic vocabulary). `limiting_factor` and
`criticality` appear in ZERO files. The level NAMES were largely
consistent; what was entirely missing was COMPOSITION -- no rule
anywhere says what confidence a derived value has when one of its
inputs is stale, gapped or unknown. Uncertainty therefore RESET at
every layer boundary instead of propagating.

This module supplies the missing algebra. It does NOT replace any
existing vocabulary: `adapters.py` maps the existing ones onto this
model, and no package is rewritten.

THE CENTRAL RULE: a derived belief may never be stronger than its
weakest CRITICAL input, and whenever it is weakened, it must name what
weakened it. That is what makes "why does Bujji believe this, and what
is the weakest evidence supporting it?" answerable.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping, Optional, Tuple

SCHEMA_VERSION = "1.0.0"

# --- Epistemic state ---------------------------------------------------------
KNOWN = "KNOWN"                              # evidence exists and resolved
UNKNOWN = "UNKNOWN"                          # evidence SHOULD exist, does not
NOT_AVAILABLE = "NOT_AVAILABLE"              # predates the feature/schema that would capture it
NOT_APPLICABLE = "NOT_APPLICABLE"            # legitimately no such evidence for this object
INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"  # needs N observations, has fewer -- SELF-HEALS
STALE = "STALE"                              # resolved, but older than its freshness bound
GAP = "GAP"                                  # the source window has a hole
DEGRADED = "DEGRADED"                        # resolved from partially-trustworthy inputs

ALL_STATES = (KNOWN, UNKNOWN, NOT_AVAILABLE, NOT_APPLICABLE,
              INSUFFICIENT_HISTORY, STALE, GAP, DEGRADED)

# States that carry a usable value. Everything else means "no number".
VALUE_BEARING = (KNOWN, STALE, GAP, DEGRADED)

# States a consumer must never silently treat as evidence of absence.
# UNKNOWN means "we don't know", which is NOT "there is nothing".
NON_EVIDENCE = (UNKNOWN, NOT_AVAILABLE, INSUFFICIENT_HISTORY)

# --- Confidence --------------------------------------------------------------
HIGH = "HIGH"
MODERATE = "MODERATE"
LOW = "LOW"
NONE = "NONE"
ALL_CONFIDENCE = (HIGH, MODERATE, LOW, NONE)

_RANK = {NONE: 0, LOW: 1, MODERATE: 2, HIGH: 3}
_BY_RANK = {v: k for k, v in _RANK.items()}


def rank(confidence: str) -> int:
    return _RANK.get(confidence, 0)


def weaker(a: str, b: str) -> str:
    """The weaker of two confidences. Basis of the MIN rule."""
    return a if rank(a) <= rank(b) else b


def demote(confidence: str, bands: int = 1) -> str:
    """Drop `bands` levels, floored at NONE."""
    return _BY_RANK[max(0, rank(confidence) - bands)]


def cap(confidence: str, ceiling: str) -> str:
    """Never above `ceiling`."""
    return confidence if rank(confidence) <= rank(ceiling) else ceiling


# --- The carrier -------------------------------------------------------------
@dataclass(frozen=True)
class Uncertainty:
    """Travels WITH a derived value; never inferred after the fact.

    `limiting_factor` is mandatory whenever confidence was reduced --
    it names WHICH input capped this belief. Without it, a degraded
    confidence is an unexplainable number, and the whole point of this
    model is explainability.
    """

    state: str = KNOWN
    confidence: str = HIGH
    limiting_factor: Optional[str] = None
    freshness_s: Optional[float] = None
    completeness: Optional[float] = None        # 0.0-1.0 fraction of expected inputs resolved
    provenance: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.state not in ALL_STATES:
            raise ValueError(f"unknown epistemic state {self.state!r}")
        if self.confidence not in ALL_CONFIDENCE:
            raise ValueError(f"unknown confidence {self.confidence!r}")

    @property
    def carries_value(self) -> bool:
        return self.state in VALUE_BEARING

    @property
    def is_actionable(self) -> bool:
        """Risk and execution layers must gate on THIS, not on `state`
        alone. STALE and GAP carry values that are fine for analysis
        and unfit for committing capital."""
        return self.state == KNOWN and rank(self.confidence) >= rank(MODERATE)

    def to_dict(self) -> dict:
        return {
            "state": self.state, "confidence": self.confidence,
            "limiting_factor": self.limiting_factor, "freshness_s": self.freshness_s,
            "completeness": self.completeness, "provenance": list(self.provenance),
        }

    @staticmethod
    def from_dict(d: Mapping) -> "Uncertainty":
        return Uncertainty(
            state=d.get("state", KNOWN), confidence=d.get("confidence", HIGH),
            limiting_factor=d.get("limiting_factor"), freshness_s=d.get("freshness_s"),
            completeness=d.get("completeness"), provenance=tuple(d.get("provenance") or ()),
        )


@dataclass(frozen=True)
class Input:
    """One contributing input to a derivation.

    `critical` is declared PER FEATURE, never globally. Everything-critical
    collapses to UNKNOWN constantly; nothing-critical launders uncertainty.
    A feature definition is incomplete without its criticality map.
    """

    name: str
    uncertainty: Uncertainty
    critical: bool = True


# --- Composition -------------------------------------------------------------
def unknown(reason: str, provenance: Tuple[str, ...] = ()) -> Uncertainty:
    return Uncertainty(state=UNKNOWN, confidence=NONE, limiting_factor=reason,
                       provenance=provenance)


def insufficient(reason: str, provenance: Tuple[str, ...] = ()) -> Uncertainty:
    """INSUFFICIENT_HISTORY is deliberately distinct from UNKNOWN: it
    self-heals as observations accumulate, and must never be learned
    from as though the answer were unknowable."""
    return Uncertainty(state=INSUFFICIENT_HISTORY, confidence=NONE,
                       limiting_factor=reason, provenance=provenance)


def compose(inputs: Iterable[Input], *,
            base_confidence: str = HIGH,
            range_dependent: bool = False) -> Uncertainty:
    """Derive the uncertainty of an output from its inputs.

    Rules, applied in order:

      1. NOT_APPLICABLE inputs are IGNORED entirely -- they do not
         degrade siblings. "No management event ever happened" is not
         missing evidence.
      2. Any CRITICAL input in a non-evidence state (UNKNOWN /
         NOT_AVAILABLE / INSUFFICIENT_HISTORY) -> output absorbs that
         state. No value is emitted. Never averaged away.
      3. GAP in any critical input taints the output: state GAP,
         confidence capped at LOW. If the output is `range_dependent`
         (ATR, Bollinger, realised vol -- anything whose definition
         spans the window) the gap BREAKS the definition, so the output
         becomes UNKNOWN rather than a low-confidence number.
      4. STALE in any critical input taints: state STALE, confidence
         capped at LOW.
      5. MIN rule: confidence never exceeds the weakest critical input.
      6. Non-critical inputs in a non-evidence state do not block, but
         cost one confidence band and reduce `completeness`.
      7. `limiting_factor` names whichever input actually capped the
         result. Mandatory whenever the output is below `base_confidence`.
    """
    considered = [i for i in inputs if i.uncertainty.state != NOT_APPLICABLE]
    if not considered:
        return Uncertainty(state=base_confidence and KNOWN, confidence=base_confidence,
                           completeness=1.0)

    critical = [i for i in considered if i.critical]
    non_critical = [i for i in considered if not i.critical]
    provenance = tuple(p for i in considered for p in i.uncertainty.provenance)

    # 2. critical non-evidence -> absorption
    for i in critical:
        if i.uncertainty.state in NON_EVIDENCE:
            return Uncertainty(
                state=i.uncertainty.state, confidence=NONE,
                limiting_factor=f"{i.name}:{i.uncertainty.state}",
                completeness=_completeness(considered), provenance=provenance,
            )

    confidence = base_confidence
    state = KNOWN
    limiter: Optional[str] = None

    # 5. MIN rule over critical inputs
    for i in critical:
        if rank(i.uncertainty.confidence) < rank(confidence):
            confidence = i.uncertainty.confidence
            limiter = f"{i.name}:confidence={i.uncertainty.confidence}"

    # 3. GAP taint
    gapped = [i for i in critical if i.uncertainty.state == GAP]
    if gapped:
        if range_dependent:
            return Uncertainty(
                state=UNKNOWN, confidence=NONE,
                limiting_factor=f"{gapped[0].name}:GAP breaks a range-dependent definition",
                completeness=_completeness(considered), provenance=provenance,
            )
        state = GAP
        confidence = cap(confidence, LOW)
        limiter = f"{gapped[0].name}:GAP"

    # 4. STALE taint
    stale = [i for i in critical if i.uncertainty.state == STALE]
    if stale and state == KNOWN:
        state = STALE
        confidence = cap(confidence, LOW)
        limiter = f"{stale[0].name}:STALE"

    # 6. non-critical degradation
    missing_non_critical = [i for i in non_critical if i.uncertainty.state in NON_EVIDENCE]
    if missing_non_critical:
        demoted = demote(confidence)
        if rank(demoted) < rank(confidence):
            limiter = f"{missing_non_critical[0].name}:{missing_non_critical[0].uncertainty.state}(non-critical)"
        confidence = demoted
        if state == KNOWN:
            state = DEGRADED

    freshness = max((i.uncertainty.freshness_s for i in considered
                     if i.uncertainty.freshness_s is not None), default=None)

    return Uncertainty(
        state=state, confidence=confidence,
        limiting_factor=limiter if rank(confidence) < rank(base_confidence) or state != KNOWN else None,
        freshness_s=freshness, completeness=_completeness(considered), provenance=provenance,
    )


def _completeness(inputs) -> float:
    if not inputs:
        return 1.0
    resolved = sum(1 for i in inputs if i.uncertainty.state in VALUE_BEARING)
    return round(resolved / len(inputs), 4)


def stale_if_older_than(u: Uncertainty, age_s: float, bound_s: float) -> Uncertainty:
    """Mark STALE when an otherwise-KNOWN value exceeds its freshness
    bound. The value is RETAINED -- analysis may use it, but
    `is_actionable` becomes False so risk/execution cannot."""
    if u.state != KNOWN or age_s <= bound_s:
        return Uncertainty(state=u.state, confidence=u.confidence,
                           limiting_factor=u.limiting_factor, freshness_s=age_s,
                           completeness=u.completeness, provenance=u.provenance)
    return Uncertainty(state=STALE, confidence=cap(u.confidence, LOW),
                       limiting_factor=f"freshness:{age_s:.1f}s>{bound_s:.1f}s",
                       freshness_s=age_s, completeness=u.completeness, provenance=u.provenance)
