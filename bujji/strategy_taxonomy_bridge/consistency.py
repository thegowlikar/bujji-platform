"""Selection x Eligibility Consistency Check — Phase 14B P0.3. Pure
function, no state, no IO. Read-only reporting layer: never mutates,
never gates, never feeds back into Selection/Eligibility/TradeIntent --
mirrors bujji.intelligence_consistency_checker's own established
report-only discipline.

Critical honesty rule (P0.4): UNKNOWN must never collapse into NO.
Eligibility ALWAYS returns a conclusive eligible/ineligible partition
of all 8 of its own families whenever it runs at all (see
msi_strategy_eligibility.engine._families_for_gate) -- so "UNKNOWN"
here specifically means "the read that partition would be dishonest to
trust" (no eligibility computed this cycle, or eligibility_confidence
is NONE), never "eligibility said no." That distinction is preserved
explicitly below.
"""
from __future__ import annotations

from typing import Optional

from bujji.strategy_taxonomy_bridge.mapping import eligibility_families_for_selection_family

STATUS_NOT_APPLICABLE = "NOT_APPLICABLE"          # no strategy was selected this cycle -- nothing to check.
STATUS_UNKNOWN = "UNKNOWN"                        # no eligibility data exists this cycle at all.
STATUS_ELIGIBILITY_UNRESOLVED = "ELIGIBILITY_UNRESOLVED"  # eligibility ran but its own confidence is NONE.
STATUS_ELIGIBLE = "ELIGIBLE"
STATUS_NOT_ELIGIBLE = "NOT_ELIGIBLE"
STATUS_NOT_MAPPED = "NOT_MAPPED"                  # defensive: selected family has no bridge entry at all.


def check_selection_eligibility_consistency(
    selected_family: Optional[str],
    eligibility: Optional[dict],
) -> dict:
    """Pure function: Selection's pick (plain string or None) + the
    persisted `strategy_eligibility` dict for the SAME cycle -> a
    single verdict, never inferred, never fabricated. `eligibility` is
    the already-serialized dict shape (as persisted in
    intelligence_cycle.jsonl), matching this project's established
    read-only-over-persisted-dicts pattern (bujji.intelligence_completeness,
    bujji.intelligence_consistency_checker)."""
    if selected_family is None:
        return {"status": STATUS_NOT_APPLICABLE, "selected_family": None, "mapped_eligibility_families": (), "reason": "no strategy was selected this cycle"}

    if not isinstance(eligibility, dict):
        return {"status": STATUS_UNKNOWN, "selected_family": selected_family, "mapped_eligibility_families": (), "reason": "no strategy_eligibility assessment exists this cycle"}

    eligibility_confidence = eligibility.get("eligibility_confidence")
    if eligibility_confidence == "NONE":
        return {
            "status": STATUS_ELIGIBILITY_UNRESOLVED, "selected_family": selected_family,
            "mapped_eligibility_families": (), "reason": "eligibility_confidence is NONE -- its eligible/ineligible split cannot be trusted this cycle",
        }

    mapped = eligibility_families_for_selection_family(selected_family)
    if not mapped:
        return {"status": STATUS_NOT_MAPPED, "selected_family": selected_family, "mapped_eligibility_families": (), "reason": f"{selected_family} has no declared taxonomy-bridge mapping"}

    eligible_set = set(eligibility.get("eligible_strategy_families") or ())
    permitted = tuple(f for f in mapped if f in eligible_set)

    if permitted:
        return {"status": STATUS_ELIGIBLE, "selected_family": selected_family, "mapped_eligibility_families": permitted, "reason": f"permitted via {permitted}"}
    return {
        "status": STATUS_NOT_ELIGIBLE, "selected_family": selected_family, "mapped_eligibility_families": mapped,
        "reason": f"none of {mapped} are in eligible_strategy_families={sorted(eligible_set)}",
    }
