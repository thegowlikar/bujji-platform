"""Decision Artifact — BUJJI Options OS v3.

The single evidence trail per decision cycle: composes
`intelligence_orchestrator.DecisionTrace` + `market_thesis.
MarketThesisAssessment` + `RankedCandidates` + optional real trade
construction/outcome objects into one immutable, journaled record.
See engine.py for the full field-by-field provenance.
"""
from .engine import build_decision_artifact
from .journal import DecisionArtifactJournal
from .models import DecisionArtifact

__all__ = ["build_decision_artifact", "DecisionArtifactJournal", "DecisionArtifact"]
