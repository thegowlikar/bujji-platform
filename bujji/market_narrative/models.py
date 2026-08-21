"""Market Narrative -- Phase 11 Upgrade 2. Pure models, no IO.

A structured, rule-based (no LLM) narrative built ONLY from fragments
that resolved with real evidence this cycle -- never invents a sentence
about a domain that reported UNKNOWN/None. Silence, not fabrication,
for anything unresolved.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple


@dataclass(frozen=True)
class NarrativeReport:
    market_story: str
    dominant_factors: Tuple[str, ...]
    contradictions: Tuple[str, ...]
    confidence: str

    def to_dict(self) -> dict:
        return {
            "market_story": self.market_story,
            "dominant_factors": list(self.dominant_factors),
            "contradictions": list(self.contradictions),
            "confidence": self.confidence,
        }
