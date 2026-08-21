"""Phase 20.22 -- Memory Context Integration Layer.

Connects existing Memory Intelligence output (Phase 20.15.1) into
future decision context WITHOUT changing decision authority. A thin
composition layer only -- no memory evaluation logic exists anywhere
in this package.

STEP 1 AUDIT SUMMARY (see docs/PHASE_20_22_MEMORY_CONTEXT_INTEGRATION_REPORT.md
for the full audit):

- `bujji.memory_intelligence` (Phase 20.15.1) -- A) reusable directly,
  and NOT duplicated. Its `MemoryInfluenceAssessment`,
  `evaluate_memory_influence()`, and `apply_memory_influence()` already
  fully implement bounded one-band confidence adjustment, support/
  contradiction handling, `evidence_score` protection, and
  explainability -- confirmed, tested (10 tests), and real-data
  validated in that phase. The Step 1 audit for THIS phase confirmed
  the actual gap is integration, not intelligence: `apply_memory_
  influence()` is defined and tested but is never called by any real
  consumer anywhere in the codebase (verified by grepping every
  importer). This package is that missing consumer -- it imports
  `MemoryInfluenceAssessment` directly and computes nothing about
  memory itself.
- `bujji.decision_context.DecisionContext` (Phase 19.4, MSI lineage)
  -- C) wrong domain, disclosed naming precedent. A real, separate
  class bridging a Strategy Engine's own evidence interpretation to
  compatible strategy families -- a different pipeline entirely. This
  package's own `MemoryDecisionContext` uses a distinct name
  specifically to avoid ambiguity with it (same precedent as
  `ShadowResultRecord` avoiding the bare `ShadowResult` name).
- `bujji.intelligence.context.IntelligenceContext` (Phase 19.2.2, MSI
  lineage) -- C) wrong domain. A shared clock/reference object for
  that lineage's own brains. Not imported, not extended.
- `bujji.decision_orchestration.FinalDecision` (Phase 20.10) -- A)
  reusable directly, this package's other input. Never recomputed,
  never modified. `decision_status` on `MemoryDecisionContext` is
  always a verbatim passthrough of `FinalDecision.decision_state`.

This package NEVER calculates memory similarity, queries an
EventStore, evaluates historical outcomes, or modifies evidence_score/
qualification/ranking/allocation. It NEVER turns a rejected decision
(`NO_OPPORTUNITY`/`BLOCKED`/`INSUFFICIENT_INTELLIGENCE`) into an
eligible one, NEVER removes a risk restriction, and NEVER creates
execution capability -- `MemoryDecisionContext` is a VIEW object; it
cannot replace or override `FinalDecision`.
"""
from .adapter import build_memory_decision_context
from .explain import explain_memory_decision_context
from .models import MemoryDecisionContext

__all__ = ["build_memory_decision_context", "explain_memory_decision_context", "MemoryDecisionContext"]
