"""bujji.msi_knowledge_validation -- Knowledge Validation Engine (KVE),
Series 105.

KVE does NOT invent ideas. KVE validates recurring evidence. Every real
candidate hypothesis (a caller-supplied sequence of real
HypothesisOccurrence records, each citing real Series 101-104 artefact
ids) receives exactly one of seven validation states: NOT_OBSERVED,
OBSERVED, REPEATED, EMERGING, VALIDATED, DECAYING, INVALIDATED.

The most isolated package in the whole learning stack (tied with Series
104's OAE): ZERO imports from any other bujji package, anywhere in this
package. KVE consumes real Series 100-104 outputs only via the
caller-supplied `HypothesisOccurrence` translation -- never Production,
never raw market feeds, never another sibling package's own types.
Never imported by any Production module (verified by
tests/test_kve_isolation.py).

Generates NO Knowledge Candidate, NO Engineering Proposal, NO
recommendation -- structurally impossible, since no such model type
exists in this package. Per the mission's own philosophy, that synthesis
belongs to a later, separate series -- deliberately not this one.

See docs/KNOWLEDGE_VALIDATION_ENGINE_ARCHITECTURE.md for the full spec.
"""
from __future__ import annotations
