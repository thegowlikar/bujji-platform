"""Phase 20.29 -- Position Construction <-> Trade Construction wiring bridge.

Closes the gap the Phase 20.28/20.29 audits found: `msi_position_construction`
(Series 95) computes a real `construction_type` shape decision, and it was
being discarded -- `msi_trade_construction.construct_trade()` (Series 90)
independently re-derives its own hardcoded shape per family, with no
parameter to receive Series 95's decision at all.

NEITHER Series 95 nor Series 90 is modified by this package. Both are
called exactly as they already exist, unmodified real functions.

IMPORTANT LESSON FROM THIS PHASE'S OWN FIRST ATTEMPT: `msi_trade_construction`
is explicitly PROTECTED -- `tests/test_shadow_trade_construction_safety.py::
test_msi_trade_construction_engine_not_modified` enforces byte-identity
against a locked baseline commit, because `bujji.shadow_trade_construction`
was already built assuming that engine never changes. An initial attempt to
add a `construction_type` parameter directly to `construct_trade()` broke
that safety test; it was reverted immediately (git checkout, confirmed
byte-identical again) rather than weakening the test. This package is the
correct fix: an external, additive bridge -- the same "thin adapter, never
modify the reused engine" pattern every other Phase 20.x bridge in this
engagement already uses (`mic_context_bridge`, `risk_context_adapter`,
`mic_runtime_context`, ...).

Behavior: when Series 95's `construction_type` matches what Series 90's
real, unmodified leg-construction logic already builds for that family
(true for every family except the two directional ones, per this phase's
own capability trace), the real `construct_trade()` is called and its
real result returned unchanged. When it does NOT match (Series 95 asking
for a defined-risk spread on a directional family, which Series 90's real
code has no leg-construction logic for), this bridge fails closed with an
honest, disclosed rejection BEFORE `construct_trade()` is ever called --
never silently building a different shape than what Position Construction
actually decided, never silently ignoring the mismatch.

Phase 20.31 addition: a second, sibling wire,
`construct_trade_honoring_structure_selection`, carrying
`bujji.premium_structure_intelligence.select_structure()`'s real
`StructureSelectionAssessment` into the same real, unmodified Series 90
entrypoint -- same fail-closed discipline, same "call the real function
or refuse honestly" boundary. `construct_trade_honoring_position_plan`
is untouched by this addition.
"""
from .bridge import (
    construct_trade_honoring_position_plan, construct_trade_honoring_structure_selection,
    STRUCTURE_TO_FAMILY, SUPPORTED_CONSTRUCTION_TYPE_PER_FAMILY,
)

__all__ = [
    "construct_trade_honoring_position_plan", "construct_trade_honoring_structure_selection",
    "STRUCTURE_TO_FAMILY", "SUPPORTED_CONSTRUCTION_TYPE_PER_FAMILY",
]
