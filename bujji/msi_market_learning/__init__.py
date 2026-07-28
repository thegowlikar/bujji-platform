"""bujji.msi_market_learning -- Market Learning Engine (MLE), Series 100,
Phase 1.0.

MLE is an EVIDENCE ENGINE, not a learning/adaptive system: it converts
real trading experience (via bujji.msi_decision_auditor's real
DecisionOutcomePair records) into disclosed, traceable Knowledge
Candidates. It NEVER writes to, imports from the write-path of, or is
imported by any Production decision module -- enforced structurally and
verified by tests/test_mle_isolation.py, not by convention alone.

No Production behaviour may ever change because of this package. See
docs/MARKET_LEARNING_ENGINE_ARCHITECTURE.md for the full specification.
"""
from __future__ import annotations
