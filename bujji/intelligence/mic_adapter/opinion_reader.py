"""Opinion Reader — BUJJI Options OS, Integration Series 4, Sprint 1.

Reads MIC v2's published `MarketOpinion` classification (Engineering
Series 19, Sprint 1 / Addendum 8) given only an `opinion_id` reference
already carried on an `IntelligenceSnapshot`. Read-only, no mutation, no
caching -- re-reads the journal fresh on every call, matching
`IntelligenceAdapter.load_latest_snapshot()`'s own discipline. Any
failure (missing file, malformed line, id not found, exception of any
kind) returns None -- never raises, never blocks a trading decision.

This is the SECOND (and, as of this sprint, final) mic_v2 import
anywhere in the mic_adapter package -- alongside the existing
mic_v2.journal.consumer_journal.ConsumerJournal import in
mic_adapter/query.py. Both are named explicitly in
tests/test_intelligence_adapter.py's own isolation test; this is a
deliberate, disclosed relaxation of the prior "exactly one mic_v2
import" invariant, not an accidental loosening.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

BULLISH = "BULLISH"
BEARISH = "BEARISH"
NEUTRAL = "NEUTRAL"

# MIC v2's own 6-state taxonomy (mic_v2.opinion.taxonomy) maps onto
# BUJJI's 3-state Direction enum (bujji.core.enums.Direction) for
# exactly these three values -- copied verbatim, never coerced.
# MIXED / INSUFFICIENT_EVIDENCE / UNKNOWN have no honest BUJJI-side
# equivalent and translate to None, which IntelligencePolicy classifies
# as ABSTAINED ("no_directional_opinion_published") -- never fabricated
# into NEUTRAL or any other value.
_PASSTHROUGH_CLASSIFICATIONS = (BULLISH, BEARISH, NEUTRAL)


def translate_classification(mic_classification: Optional[str]) -> Optional[str]:
    """Pure function. Translates MIC v2's 6-state MarketOpinion
    taxonomy into BUJJI's 3-state Direction taxonomy, or None when no
    honest equivalent exists (MIXED / INSUFFICIENT_EVIDENCE / UNKNOWN /
    anything else unrecognized)."""
    if mic_classification in _PASSTHROUGH_CLASSIFICATIONS:
        return mic_classification
    return None


def _ensure_mic_v2_importable(mic_v2_root: Path) -> None:
    root_str = str(mic_v2_root)
    if root_str not in sys.path:
        sys.path.insert(0, root_str)


def read_opinion_classification(opinion_id: Optional[str], opinion_journal_path: Path, mic_v2_root: Path) -> Optional[str]:
    """Reads MIC v2's Opinion Journal fresh (never cached) and returns
    the classification for `opinion_id`, translated into BUJJI's
    3-state taxonomy via `translate_classification()`. Returns None on
    any failure -- missing opinion_id, missing/unreadable journal file,
    malformed line, or the id simply not being present. Mirrors
    `query.py::read_consumer_records()`'s own discipline exactly:
    explicit `mic_v2_root`/journal path parameters, no config
    constructed internally, one narrowly-scoped import.
    """
    if not opinion_id:
        return None
    try:
        _ensure_mic_v2_importable(mic_v2_root)
        from mic_v2.journal.opinion_journal import OpinionJournal  # Opinion Publication surface only.

        journal = OpinionJournal(opinion_journal_path)
        for opinion in journal.read_all():
            if opinion.opinion_id == opinion_id:
                return translate_classification(opinion.classification)
        return None
    except Exception:  # noqa: BLE001 - observational only, must never block trading.
        return None
