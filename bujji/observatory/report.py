"""Market Intelligence Observatory — Report Orchestration.

BUJJI Options OS v3, Engineering Series 66.

Read-only. Composes `explanation.py`/`comparison.py`/`timeline.py`
over already-saved qualification reports (Series 58/65's own
`reports/*.json` files) into one observatory report. Never recomputes
a replay, never reads a corpus directly -- only already-recorded
qualification report JSON.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from typing import Any, Callable, Dict, Optional, Tuple

from .comparison import SessionDiff, compare_corpora, compare_sessions
from .explanation import ClassificationExplanation, explain_session
from .timeline import ReasoningTimeline, build_timeline, explain_trading_impact

Clock = Callable[[], datetime]


def _real_clock() -> datetime:
    return datetime.now()


def load_qualification_report(path: str) -> Dict[str, Any]:
    """Load an already-saved qualification report JSON file verbatim
    -- read-only, never modifies the file, never recomputes its
    contents.
    """
    with open(path) as f:
        return json.load(f)


def explain_one_session(report: Dict[str, Any], session_id: str) -> Tuple[ClassificationExplanation, ...]:
    session = next((s for s in report["sessions"] if s["id"] == session_id), None)
    if session is None:
        raise ValueError(f"Session {session_id!r} not found in report")
    return explain_session(session)


def build_demo_report(
    report_a_path: str,
    report_b_path: str,
    focus_session_id: str,
    label_a: str = "corpus A",
    label_b: str = "corpus B",
    clock: Clock = _real_clock,
) -> Dict[str, Any]:
    """Build the Series 66 demonstration: compare two already-saved
    qualification reports session-by-session, and explain the focus
    session in detail. Read-only over both input files.
    """
    report_a = load_qualification_report(report_a_path)
    report_b = load_qualification_report(report_b_path)

    diffs = compare_corpora(tuple(report_a["sessions"]), tuple(report_b["sessions"]))
    changed_diffs = tuple(d for d in diffs if d.changed_fields)

    focus_session_a = next((s for s in report_a["sessions"] if s["id"] == focus_session_id), None)
    focus_session_b = next((s for s in report_b["sessions"] if s["id"] == focus_session_id), None)
    focus_diff = compare_sessions(focus_session_a, focus_session_b) if focus_session_a and focus_session_b else None
    focus_narrative = (
        explain_trading_impact(focus_diff, focus_session_a, focus_session_b)
        if focus_diff
        else "Focus session not present in both reports."
    )

    focus_explanation_a = explain_session(focus_session_a) if focus_session_a else ()
    focus_explanation_b = explain_session(focus_session_b) if focus_session_b else ()

    timeline_a = build_timeline(focus_session_a) if focus_session_a else None
    timeline_b = build_timeline(focus_session_b) if focus_session_b else None

    return {
        "generated_at": clock().isoformat(),
        "corpus_a": {"label": label_a, "source": report_a_path, "session_count": len(report_a["sessions"])},
        "corpus_b": {"label": label_b, "source": report_b_path, "session_count": len(report_b["sessions"])},
        "total_sessions_compared": len(diffs),
        "sessions_with_no_change": sum(1 for d in diffs if not d.changed_fields),
        "sessions_with_change": len(changed_diffs),
        "focus_session": {
            "id": focus_session_id,
            "narrative": focus_narrative,
            "diff": asdict(focus_diff) if focus_diff else None,
            "explanation_corpus_a": [asdict(e) for e in focus_explanation_a],
            "explanation_corpus_b": [asdict(e) for e in focus_explanation_b],
            "timeline_corpus_a": asdict(timeline_a) if timeline_a else None,
            "timeline_corpus_b": asdict(timeline_b) if timeline_b else None,
        },
        "all_changed_sessions": [
            {
                "session_id": d.session_id,
                "changed_fields": d.changed_fields,
                "first_divergence": d.first_divergence,
                "downstream_impact": d.downstream_impact,
            }
            for d in changed_diffs
        ],
    }
