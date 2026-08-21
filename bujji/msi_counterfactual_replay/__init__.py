"""bujji.msi_counterfactual_replay -- Counterfactual Replay Engine (CRE),
Series 102.

CRE never invents decisions, never optimises decisions, never changes
Production -- it only replays real, causal, production-valid alternative
decision paths using the real, unmodified frozen decision pipeline.

Isolation: NEVER imported by any Production module (the one permanently-
forbidden path, verified by tests/test_cre_isolation.py). This package
DOES import real Production decision functions -- unlike Series 100/101,
that is CRE's entire purpose -- but that surface is deliberately isolated
to one file, `replay.py`, reviewed independently; `engine.py` and every
other file in this package stay pure and Production-import-free.
Order-placing functions are never referenced anywhere in this package.

Output artefacts (CounterfactualSession) are consumed by MLE (Series
100), cited by Evidence Packets (Series 101), and interpreted by
Knowledge Candidates -- CRE itself interprets nothing.

See docs/COUNTERFACTUAL_REPLAY_ENGINE_ARCHITECTURE.md for the full spec.
"""
from __future__ import annotations
