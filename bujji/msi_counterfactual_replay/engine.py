"""CRE engine — Series 102. Pure functions: no IO, no state, no
wall-clock reads, no randomness. Zero imports from any Production
decision/execution/broker package -- unlike `replay.py` (the one,
disclosed, isolated exception in this package), this module never calls
a real decision function itself; it only validates and assembles
ExploredPath data the caller already produced."""
from __future__ import annotations

import hashlib
from typing import Sequence, Tuple

from . import config as _config
from . import taxonomy
from .models import CounterfactualSession, ExploredPath


def validate_causality(path: ExploredPath) -> Tuple[bool, Tuple[str, ...]]:
    """The one, disclosed causality rule: every real data timestamp this
    path used must be <= its own decision_timestamp. No future candle,
    Greek, IV read, or market state may ever be used -- checked
    literally, not assumed."""
    violations = tuple(
        f"future data used on path {path.path_id}: timestamp {ts} > decision_timestamp {path.decision_timestamp}"
        for ts in path.data_timestamps_used if ts > path.decision_timestamp
    )
    if violations:
        return False, violations
    return True, (f"path {path.path_id}: all {len(path.data_timestamps_used)} real data timestamp(s) "
                   f"<= decision_timestamp {path.decision_timestamp}",)


def validate_legality(explored_paths: Sequence[ExploredPath]) -> Tuple[bool, Tuple[str, ...]]:
    """Phase 1's own, disclosed legal decision space: exactly one
    BASELINE and one ALTERNATIVE path, never more (no exhaustive search),
    never fewer (a session needs at least a real comparison to mean
    anything)."""
    reasons = []
    if len(explored_paths) != taxonomy.PHASE1_MAX_EXPLORED_PATHS:
        reasons.append(
            f"Phase 1 supports exactly NO_TRADE vs ONE alternative path "
            f"({taxonomy.PHASE1_MAX_EXPLORED_PATHS} total) -- got {len(explored_paths)}"
        )
    labels = tuple(p.label for p in explored_paths)
    if labels.count(taxonomy.PATH_LABEL_BASELINE) != 1:
        reasons.append(f"expected exactly 1 {taxonomy.PATH_LABEL_BASELINE} path, found {labels.count(taxonomy.PATH_LABEL_BASELINE)}")
    if labels.count(taxonomy.PATH_LABEL_ALTERNATIVE) != 1:
        reasons.append(f"expected exactly 1 {taxonomy.PATH_LABEL_ALTERNATIVE} path, found {labels.count(taxonomy.PATH_LABEL_ALTERNATIVE)}")
    if reasons:
        return False, tuple(reasons)
    return True, ("exactly one BASELINE and one ALTERNATIVE path -- Phase 1 legal decision space satisfied",)


def _session_id(replay_id: str, baseline_ts: str, alternative_ts: str, path_ids: Tuple[str, ...], schema_version: str) -> str:
    content = "|".join([replay_id, baseline_ts, alternative_ts, ",".join(path_ids), schema_version])
    return "CFS-" + hashlib.md5(content.encode("utf-8")).hexdigest()[:24]


def build_counterfactual_session(
    *, replay_id: str, baseline_path: ExploredPath, alternative_path: ExploredPath,
    production_version: str, replay_version: str, supporting_references: Sequence[str],
    schema_version: str = _config.SCHEMA_VERSION,
) -> CounterfactualSession:
    """Assembles exactly one real CounterfactualSession from two real,
    already-computed ExploredPath records. Runs BOTH validators for real
    and records the honest verdict -- an ILLEGAL or acausal session is
    still recorded and disclosed, never hidden or silently discarded
    (mirrors this project's fail-closed-and-disclose convention, e.g.
    freshness STALE states)."""
    explored = (baseline_path, alternative_path)
    legality_ok, legality_reasons = validate_legality(explored)

    causality_verdicts = []
    all_causal = True
    for path in explored:
        ok, reasons = validate_causality(path)
        all_causal = all_causal and ok
        causality_verdicts.extend(reasons)

    is_legal = legality_ok and all_causal
    legality = taxonomy.LEGALITY_LEGAL if is_legal else taxonomy.LEGALITY_ILLEGAL

    all_data_timestamps = baseline_path.data_timestamps_used + alternative_path.data_timestamps_used
    earliest = min(all_data_timestamps) if all_data_timestamps else min(baseline_path.decision_timestamp, alternative_path.decision_timestamp)

    path_ids = tuple(sorted(p.path_id for p in explored))
    sid = _session_id(replay_id, baseline_path.decision_timestamp, alternative_path.decision_timestamp, path_ids, schema_version)

    assumptions = (
        "uses only real, already-recorded intraday candles and Bhavcopy chain data -- never fabricated",
        "no data timestamp exceeds its own path's decision_timestamp (causality, validated above)",
        "Production decision functions invoked unmodified, exactly as they run live",
        "Phase 1 scope: NO_TRADE vs exactly one alternative production-valid path -- no exhaustive search, no optimisation, no ranking",
    )
    rejected_paths = (
        "exhaustive multi-path / multi-strategy search: rejected, out of Phase 1 scope per the Series 102 mission",
    )

    return CounterfactualSession(
        session_id=sid, replay_id=replay_id, replay_timestamp=alternative_path.decision_timestamp,
        baseline_timestamp=baseline_path.decision_timestamp, production_version=production_version,
        replay_version=replay_version, assumptions=assumptions, explored_paths=explored,
        rejected_paths=rejected_paths, replay_legality=legality,
        legality_reasoning=legality_reasons, causality_verdicts=tuple(causality_verdicts),
        earliest_causal_timestamp=earliest, supporting_references=tuple(supporting_references),
        schema_version=schema_version,
    )
