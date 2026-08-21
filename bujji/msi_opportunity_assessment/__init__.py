"""bujji.msi_opportunity_assessment -- Opportunity Assessment Engine
(OAE), Series 104.

OAE does NOT search for missed profits. OAE assesses DECISION QUALITY
using only real, causal, contemporaneous evidence. Every real trading
day receives exactly one of five classifications: GOOD_TRADE_TAKEN,
TRADE_SUBOPTIMAL, CORRECT_STAY_OUT, OPPORTUNITY_IDENTIFIED,
TRADE_SHOULD_NOT_HAVE_OCCURRED.

The most isolated package in the whole learning stack: ZERO imports
from any other bujji package, anywhere in this package. OAE consumes
real Series 99-103 artefacts only via caller-supplied, local `*View`
translations (models.py) -- the same sibling-isolation convention this
project has used since Sprint 120's ExpressionAssessmentView, taken to
its logical conclusion. Never imported by any Production module
(verified by tests/test_oae_isolation.py).

Generates NO Knowledge Candidate, NO Engineering Proposal, NO
recommendation -- structurally impossible, since no such model type
exists in this package. That synthesis is explicitly deferred to a
future, separate Series 105.

See docs/OPPORTUNITY_ASSESSMENT_ENGINE_ARCHITECTURE.md for the full spec.
"""
from __future__ import annotations
