"""Tests -- Strategy Taxonomy Bridge, Phase 14B P0.2/P0.3. Pure
functions, no state, no IO, no execution."""
from __future__ import annotations

from bujji.msi_strategy_eligibility import taxonomy as sei_taxonomy
from bujji.msi_strategy_selection_foundation import taxonomy as ssf_taxonomy
from bujji.strategy_taxonomy_bridge.consistency import (
    STATUS_ELIGIBILITY_UNRESOLVED, STATUS_ELIGIBLE, STATUS_NOT_APPLICABLE,
    STATUS_NOT_ELIGIBLE, STATUS_UNKNOWN, check_selection_eligibility_consistency,
)
from bujji.strategy_taxonomy_bridge.mapping import (
    STATUS_SUPPORTED, eligibility_families_for_selection_family,
    eligibility_family_status, selection_families_for_eligibility_family,
    selection_family_status,
)


# ---------------------------------------------------------------------------
# Test H — every Eligibility family has mapping coverage.
# ---------------------------------------------------------------------------
def test_every_eligibility_family_has_mapping_coverage():
    for family in sei_taxonomy.ALL_STRATEGY_FAMILIES:
        assert eligibility_family_status(family) == STATUS_SUPPORTED, family
        assert len(selection_families_for_eligibility_family(family)) > 0


# ---------------------------------------------------------------------------
# Test I — every Selection family has a declared compatibility status.
# ---------------------------------------------------------------------------
def test_every_selection_family_has_declared_status():
    for family in ssf_taxonomy.ALL_STRATEGY_FAMILIES:
        status = selection_family_status(family)
        assert status in ("MAPPED", "UNSUPPORTED"), family
        # in THIS mapping, every one of them is genuinely reachable.
        assert status == "MAPPED", family
        assert len(eligibility_families_for_selection_family(family)) > 0


def test_shared_calendar_name_maps_to_itself():
    assert ssf_taxonomy.CALENDAR in selection_families_for_eligibility_family(sei_taxonomy.FAMILY_CALENDAR)


def test_mapping_never_invents_a_family_outside_either_real_taxonomy():
    for eligibility_family, selection_families in [
        (f, selection_families_for_eligibility_family(f)) for f in sei_taxonomy.ALL_STRATEGY_FAMILIES
    ]:
        for sf in selection_families:
            assert sf in ssf_taxonomy.ALL_STRATEGY_FAMILIES


def test_unknown_family_name_returns_not_applicable():
    from bujji.strategy_taxonomy_bridge.mapping import STATUS_NOT_APPLICABLE
    assert eligibility_family_status("NOT_A_REAL_FAMILY") == STATUS_NOT_APPLICABLE


# ---------------------------------------------------------------------------
# Consistency check (P0.3) -- Test D/E/F.
# ---------------------------------------------------------------------------
def test_no_selection_is_not_applicable():
    result = check_selection_eligibility_consistency(None, {"eligibility_confidence": "HIGH", "eligible_strategy_families": []})
    assert result["status"] == STATUS_NOT_APPLICABLE


def test_no_eligibility_data_is_unknown_never_no():
    """Test F -- UNKNOWN eligibility remains UNKNOWN, never silently NO."""
    result = check_selection_eligibility_consistency("LONG_DIRECTIONAL", None)
    assert result["status"] == STATUS_UNKNOWN


def test_zero_confidence_eligibility_is_unresolved_never_no():
    """The mission's own explicit example: Selection=X, Eligibility=
    insufficient evidence -> ELIGIBILITY_UNRESOLVED, never ELIGIBLE or
    silently NOT_ELIGIBLE."""
    result = check_selection_eligibility_consistency(
        "LONG_DIRECTIONAL", {"eligibility_confidence": "NONE", "eligible_strategy_families": []},
    )
    assert result["status"] == STATUS_ELIGIBILITY_UNRESOLVED


def test_mapped_eligible_family_passes():
    """Test D -- a mapped eligible family passes."""
    result = check_selection_eligibility_consistency(
        "LONG_DIRECTIONAL", {"eligibility_confidence": "HIGH", "eligible_strategy_families": ["DEFINED_RISK_DIRECTIONAL"]},
    )
    assert result["status"] == STATUS_ELIGIBLE


def test_mapped_ineligible_family_fails_closed():
    """Test E -- a mapped ineligible family fails closed (NOT_ELIGIBLE,
    never silently passed through as eligible)."""
    result = check_selection_eligibility_consistency(
        "LONG_DIRECTIONAL", {"eligibility_confidence": "HIGH", "eligible_strategy_families": ["LONG_VOLATILITY"]},
    )
    assert result["status"] == STATUS_NOT_ELIGIBLE


def test_consistency_result_json_serializable():
    import json
    result = check_selection_eligibility_consistency(
        "IRON_FLY", {"eligibility_confidence": "MODERATE", "eligible_strategy_families": ["DEFINED_RISK_NEUTRAL"]},
    )
    json.dumps(result)
