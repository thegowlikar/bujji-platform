"""CRE query — Series 102. Pure, read-only lookups, mirrors every prior
MSI package's query.py convention."""
from __future__ import annotations

from typing import Sequence, Tuple

from . import taxonomy
from .models import CounterfactualSession


def legal_sessions(sessions: Sequence[CounterfactualSession]) -> Tuple[CounterfactualSession, ...]:
    return tuple(s for s in sessions if s.replay_legality == taxonomy.LEGALITY_LEGAL)


def illegal_sessions(sessions: Sequence[CounterfactualSession]) -> Tuple[CounterfactualSession, ...]:
    """Disclosed, never hidden -- a real, honest record of any session
    that failed causality or Phase 1 legal-decision-space validation."""
    return tuple(s for s in sessions if s.replay_legality == taxonomy.LEGALITY_ILLEGAL)


def by_replay_id(sessions: Sequence[CounterfactualSession], replay_id: str) -> Tuple[CounterfactualSession, ...]:
    return tuple(s for s in sessions if s.replay_id == replay_id)


def where_decision_differed(sessions: Sequence[CounterfactualSession]) -> Tuple[CounterfactualSession, ...]:
    """Real, disclosed sessions where the alternative path's real
    selected_family differs from the baseline's -- a plain filter, not an
    interpretation (MLE, not CRE, is where "was this better" gets asked)."""
    result = []
    for s in sessions:
        by_label = {p.label: p for p in s.explored_paths}
        baseline = by_label.get(taxonomy.PATH_LABEL_BASELINE)
        alternative = by_label.get(taxonomy.PATH_LABEL_ALTERNATIVE)
        if baseline and alternative and baseline.selected_family != alternative.selected_family:
            result.append(s)
    return tuple(result)
