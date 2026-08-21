"""MPC translate — Series 103. THE ONE FILE in this package permitted to
import real Production Intelligence-layer types (PriceStructureAssessment
/ MarketStructureAssessment / VolatilityStructureAssessment /
MarketDirectionAssessment). Mirrors CRE's `replay.py` isolation
convention: every other file in this package (engine.py, models.py,
journal.py, query.py) stays pure and Production-import-free; this module
is the disclosed, narrow, independently-reviewed exception.

Unlike CRE's replay.py, this module does NOT invoke any decision
function and does NOT construct trades/theses/selections -- it only
reads real, already-computed Intelligence assessment fields and
translates them into a plain MarketSnapshot. Still permanently forbidden
here, same as everywhere else: broker/execution/order-placing imports or
calls. Verified by tests/test_mpc_isolation.py.
"""
from __future__ import annotations

from typing import Optional, Sequence, Tuple

from bujji.msi_market_direction.models import MarketDirectionAssessment
from bujji.msi_market_structure.models import MarketStructureAssessment
from bujji.msi_price_structure.models import PriceStructureAssessment
from bujji.msi_volatility_structure.models import VolatilityStructureAssessment

from .models import MarketSnapshot


def snapshot_from_real_assessments(
    psi: PriceStructureAssessment, mssi: MarketStructureAssessment,
    vsb: Optional[VolatilityStructureAssessment], mdi: MarketDirectionAssessment,
    *, open_price: Optional[float] = None, previous_close_price: Optional[float] = None,
) -> MarketSnapshot:
    """Real, disclosed, field-by-field translation -- never re-derives,
    never re-classifies; every field here is a direct, real pass-through
    of an already-computed Production assessment's own field."""
    ids = tuple(sorted({psi.assessment_id, mssi.assessment_id, mdi.assessment_id} | ({vsb.assessment_id} if vsb else set())))
    return MarketSnapshot(
        timestamp=psi.timestamp,
        structure_state=psi.structure_state, compression_state=psi.compression_state,
        expansion_state=psi.expansion_state,
        structural_balance=mssi.structural_balance, structure_location=mssi.structure_location,
        volatility_regime=(vsb.volatility_regime if vsb else "UNKNOWN"),
        vsb_expansion_state=(vsb.expansion_state if vsb else "UNKNOWN"),
        vsb_compression_state=(vsb.compression_state if vsb else "UNKNOWN"),
        overall_direction=mdi.overall_direction, overall_confidence=mdi.overall_confidence,
        open_price=open_price, previous_close_price=previous_close_price,
        supporting_assessment_ids=ids,
    )


def snapshots_from_real_session(driver, *, open_price: Optional[float] = None,
                                 previous_close_price: Optional[float] = None) -> Tuple[MarketSnapshot, ...]:
    """Builds real MarketSnapshot(s) from a real, already-run
    bujji.live_pipeline_bridge.SessionDriver's own real result -- the
    driver itself is never imported at module scope here (it's passed in
    already-constructed by the caller, exactly mirroring the sibling-
    isolation discipline used throughout this project) to keep this
    function's own import surface minimal and reviewable."""
    result = driver.result
    if result.psi is None or result.mssi is None or result.mdi is None:
        return ()
    snap = snapshot_from_real_assessments(
        result.psi, result.mssi, result.vsb, result.mdi,
        open_price=open_price, previous_close_price=previous_close_price,
    )
    return (snap,)
