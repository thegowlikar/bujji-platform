"""Historical Publication Replay Driver — BUJJI Options OS v3,
Engineering Series 62 (governance evidence wired in by Series 65).

Feeds Series 61's already-verified observation payloads into MIC v2's
own frozen, already-existing downstream publication pipeline
(`mic_v2.contract.runner.run_replay_with_contract`,
`mic_v2.opinion.runner.run_replay_with_opinions`, and, as of Series 65,
`mic_v2.certification.runner.run_replay_with_certification`) -- all
already fully implemented and composed (`contract` internally composes
`lifecycle` -> `governance` -> `calibration` -> `context_stability` ->
`context`, all the way back to `evidence`; `opinion` is a sibling
branch off the same base). This driver calls each, unmodified, via
subprocess -- it never reimplements, shortcuts, translates, or
bypasses any MIC v2 logic, and no MIC v2 source file is touched.

Series 65: `derive_governance()` (`mic_v2/governance/engine.py`) has
always accepted a `certification` argument -- every previous call from
this bridge passed `None` (the only value ever supplied), which is
exactly why `governance` resolved to `REJECTED` on effectively every
real historical session across every campaign (Series 60/Campaign v2/
v2.1/Sprint B). This was a wiring gap in this bridge, not a MIC v2
defect: MIC v2's own `mic_v2.certification.runner.run_replay_with_certification()`
already exists, already runs the full pipeline with real on-disk
journals, and already certifies them -- it was simply never called
here. This sprint calls it, once per session, against a fresh
temporary replay directory, and passes the real `CertificationReport`
it returns into `run_replay_with_contract(..., certification=...)`.
Governance evidence is real from this sprint forward; nothing about
governance's own algorithm changed.

Same process boundary as Series 61: MIC v2 is never imported into this
process. The only code that ever runs inside MIC v2's own interpreter
is `_BRIDGE_SCRIPT`, passed as a `-c` argument -- never written to
disk inside MIC v2's repository.

Chronology discipline: `replay_corpus_published_states()` grows the
candle history one session at a time and re-invokes the full
publication chain for each prefix, so session N's published state
reflects only data available up to and including session N -- never a
look-ahead into later sessions. This mirrors what MIC v2 would
genuinely have published had it been running live on each historical
date.

MIC v2 Engineering Series M1 (separate repository, kept deliberately
distinct from this project's own series numbering) threaded
`option_chain_by_timestamp`/`vix_by_timestamp` through MIC v2's own
replay-mode `build_observation()` call chain -- a genuine MIC v2 change,
made in that repository, not this one. This bridge's own small,
disclosed follow-up: build those two per-timestamp maps from the same
`observation_adapter.py` (Series 68) payload fields that were already
being sent but previously discarded, and pass them to
`run_replay_with_contract()`/`run_replay_with_opinions()`. Still
transport only -- no value is recomputed, interpreted, or fabricated;
a session with no option chain or no VIX in its payload contributes
nothing to either map, exactly preserving "absent evidence stays
absent."
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

DEFAULT_MIC_V2_PYTHON = "/opt/bujji-mic-v2/.venv/bin/python"
DEFAULT_MIC_V2_CWD = "/opt/bujji-mic-v2"

# Imports exclusively from mic_v2.* -- never bujji.*. Calls MIC v2's
# own frozen `run_replay_with_contract`/`run_replay_with_opinions`/
# `run_replay_with_certification` unmodified; extracts (never
# recomputes) the latest published classification from each result.
# The certification pass runs first, in its own throwaway temp
# directory (cleaned up before this process exits), so its real
# CertificationReport can be threaded into governance -- exactly
# `derive_governance()`'s own existing, unmodified `certification`
# parameter.
_BRIDGE_SCRIPT = r"""
import json, sys, tempfile, shutil
from datetime import datetime
from mic_v2.models.evidence import Candle, OptionChainLevel
from mic_v2.contract.runner import run_replay_with_contract
from mic_v2.opinion.runner import run_replay_with_opinions
from mic_v2.certification.runner import run_replay_with_certification

payload = json.load(sys.stdin)
candles = []
option_chain_by_timestamp = {}
vix_by_timestamp = {}
for obs in payload["observations"]:
    c = obs["candle"]
    ts = datetime.fromisoformat(c["timestamp"])
    candles.append(Candle(
        timestamp=ts,
        open=c["open"], high=c["high"], low=c["low"], close=c["close"],
        volume=c.get("volume", 0.0),
    ))
    # MIC v2 Series M1: build_observation() now genuinely accepts
    # option_chain/vix_level/vix_prev_close through the replay chain.
    # Threaded here from the SAME payload fields observation_adapter.py
    # (Series 68) already built -- never recomputed, never fabricated.
    chain = obs.get("option_chain") or []
    if chain:
        option_chain_by_timestamp[ts] = tuple(
            OptionChainLevel(
                strike=level["strike"], ce_oi=level.get("ce_oi", 0), pe_oi=level.get("pe_oi", 0),
                ce_bid=level.get("ce_bid"), ce_ask=level.get("ce_ask"),
                pe_bid=level.get("pe_bid"), pe_ask=level.get("pe_ask"),
            )
            for level in chain
        )
    if "vix" in obs:
        vix_by_timestamp[ts] = (obs["vix"], obs.get("vix_prev_close"))

replay_dir = tempfile.mkdtemp(prefix="bujji_governance_certification_")
try:
    _simulation_report, certification_report = run_replay_with_certification(candles, replay_dir)
finally:
    shutil.rmtree(replay_dir, ignore_errors=True)

contract_result = run_replay_with_contract(
    candles, certification=certification_report,
    option_chain_by_timestamp=option_chain_by_timestamp, vix_by_timestamp=vix_by_timestamp,
)
# Engineering Series 70 Phase 2 (Context Stability Observatory,
# recording-only): `memories` (index 6) is already forwarded, unmodified,
# all the way through run_replay_with_lifecycle() -> run_replay_with_contract()
# -- confirmed by reading mic_v2/contract/runner.py and mic_v2/lifecycle/runner.py.
# No separate run_replay_with_memory() call is needed to obtain the full
# memory_history for this same replay; calling a second runner would risk
# a second (even if deterministic) pipeline pass never used elsewhere in this
# bridge, so this reuses the single run_replay_with_contract() result instead.
memories = contract_result[6]
contexts, stability, calibration, governance, lifecycle, contract = contract_result[-6:]

opinion_result = run_replay_with_opinions(
    candles, option_chain_by_timestamp=option_chain_by_timestamp, vix_by_timestamp=vix_by_timestamp,
)
opinions = opinion_result[-1]

def _get(obj, attr):
    return getattr(obj, attr) if obj is not None else None

latest_context = contexts[-1] if contexts else None
latest_opinion = opinions[-1] if opinions else None

# Engineering Series 70 Phase 2: per-cycle memory summary. Each
# MarketMemory already carries `reasoning_trace_id`; each MarketContext
# already carries its own `reasoning_trace_id` (context/runner.py derives
# one MarketContext per qualification cycle, matched to that cycle's own
# ReasoningTrace). Grouping memories by reasoning_trace_id and walking
# `contexts` in order recovers the per-cycle memory list without
# recomputing or re-deriving anything -- purely a regrouping of
# already-produced objects.
_memories_by_trace_id = {}
for _m in memories:
    _memories_by_trace_id.setdefault(_m.reasoning_trace_id, []).append(_m)

_FLAGGED_MEMORY_TYPES = ("REGIME_TRANSITION", "PERSISTENT_CONTRADICTION")
transition_events = []
for _ctx in contexts:
    for _m in _memories_by_trace_id.get(_ctx.reasoning_trace_id, ()):
        if _m.memory_type in _FLAGGED_MEMORY_TYPES:
            transition_events.append({
                "timestamp": _m.timestamp.isoformat(),
                "memory_type": _m.memory_type,
            })

out = {
    "market_context": _get(latest_context, "trend_context"),
    "context_id": _get(latest_context, "context_id"),
    "market_opinion": _get(latest_opinion, "classification"),
    "opinion_id": _get(latest_opinion, "opinion_id"),
    "context_stability": _get(stability, "classification"),
    "stability_id": _get(stability, "stability_id"),
    "calibration": _get(calibration, "classification"),
    "calibration_id": _get(calibration, "calibration_id"),
    "governance": _get(governance, "status"),
    "governance_id": _get(governance, "assessment_id"),
    "lifecycle": _get(lifecycle, "status"),
    "lifecycle_id": _get(lifecycle, "lifecycle_id"),
    "contract": _get(contract, "classification"),
    "contract_record_id": _get(contract, "contract_id"),
    "certification_status": _get(certification_report, "certification_status"),
    "certification_id": _get(certification_report, "certification_id"),
    "certification_deterministic": _get(certification_report, "deterministic"),
    "candle_count": len(candles),
    # Engineering Series 70 Phase 2 (Context Stability Observatory,
    # recording-only): whole-replay ContextStability detail fields --
    # already computed by mic_v2.context_stability.engine.derive_stability()
    # (called once, at the end of the replay, over the full accumulated
    # `contexts` list), simply never surfaced past `stability.classification`
    # before this sprint.
    "stability_reasoning_summary": _get(stability, "reasoning_summary"),
    "stability_transition_count": _get(stability, "transition_count"),
    "stability_persistence_length": _get(stability, "persistence_length"),
    "stability_dimension_agreement": _get(stability, "dimension_agreement"),
    "stability_confidence_variance": _get(stability, "confidence_variance"),
    "stability_context_lifetime": _get(stability, "context_lifetime"),
    # The other five of MarketContext's six per-cycle dimensions for the
    # LATEST context (trend_context already survives above as
    # "market_context"). "context_stability_dimension" is deliberately
    # named to avoid confusion with the whole-replay "context_stability"
    # key above -- it is MarketContext.stability_context, a per-cycle
    # STRING dimension, not the ContextStability object.
    "context_volatility": _get(latest_context, "volatility_context"),
    "context_liquidity": _get(latest_context, "liquidity_context"),
    "context_regime": _get(latest_context, "regime_context"),
    "context_conviction": _get(latest_context, "conviction_context"),
    "context_stability_dimension": _get(latest_context, "stability_context"),
    # Compact per-cycle memory summary, bounded to just the two flagged
    # memory_type values (REGIME_TRANSITION, PERSISTENT_CONTRADICTION) --
    # never every memory's full object, to keep the bridge payload bounded.
    "transition_events": transition_events,
}
json.dump(out, sys.stdout)
"""


@dataclass(frozen=True)
class PublishedState:
    """Everything Series 32's `PipelineInput` needs, plus the real MIC
    v2 identifiers each classification was published under -- `None`
    wherever MIC v2's own pipeline itself produced `None` (never
    substituted with a default or a guess).
    """

    market_context: Optional[str]
    market_opinion: Optional[str]
    context_stability: Optional[str]
    calibration: Optional[str]
    governance: Optional[str]
    lifecycle: Optional[str]
    contract: Optional[str]
    publication_ids: Dict[str, Optional[str]]
    candle_count: int
    certification_status: Optional[str] = None
    certification_deterministic: Optional[bool] = None
    # Engineering Series 70 Phase 2 (Context Stability Observatory,
    # recording-only): whole-replay ContextStability detail, the other
    # five MarketContext dimensions for the latest cycle, and a compact
    # per-cycle memory-transition summary -- all read verbatim off the
    # bridge script's JSON output via `.get(...)`, defaulting to `None`/
    # `()` when the bridge (an older MIC v2 checkout, or a partial
    # payload) does not supply them. Nothing here is derived or inferred;
    # see docs/CONTEXT_STABILITY_OBSERVABILITY.md.
    stability_reasoning_summary: Optional[str] = None
    stability_transition_count: Optional[int] = None
    stability_persistence_length: Optional[int] = None
    stability_dimension_agreement: Optional[float] = None
    stability_confidence_variance: Optional[float] = None
    stability_context_lifetime: Optional[int] = None
    context_volatility: Optional[str] = None
    context_liquidity: Optional[str] = None
    context_regime: Optional[str] = None
    context_conviction: Optional[str] = None
    context_stability_dimension: Optional[str] = None
    transition_events: Tuple[Dict[str, Optional[str]], ...] = ()


class PublicationReplayError(Exception):
    """Raised on any subprocess or publication failure -- never
    silently swallowed, never substituted with a fabricated
    `PublishedState`.
    """


def _invoke_bridge(observations, mic_v2_python: str, mic_v2_cwd: str, timeout_seconds: float) -> Dict:
    payload = json.dumps({"observations": list(observations)})
    try:
        proc = subprocess.run(
            [mic_v2_python, "-c", _BRIDGE_SCRIPT],
            input=payload,
            capture_output=True,
            text=True,
            cwd=mic_v2_cwd,
            timeout=timeout_seconds,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PublicationReplayError(f"Failed to invoke MIC v2 publication subprocess: {exc!r}") from exc

    if proc.returncode != 0:
        raise PublicationReplayError(f"MIC v2 publication subprocess exited {proc.returncode}: {proc.stderr}")

    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise PublicationReplayError(f"MIC v2 publication subprocess produced non-JSON output: {exc!r}") from exc


def replay_published_state_for_session(
    observations_up_to_and_including_session: Sequence[Dict],
    mic_v2_python: str = DEFAULT_MIC_V2_PYTHON,
    mic_v2_cwd: str = DEFAULT_MIC_V2_CWD,
    timeout_seconds: float = 60.0,
) -> PublishedState:
    """Replay MIC v2's publication pipeline over every observation up
    to and including the target session. Returns that session's own
    latest published classifications -- never a look-ahead, since only
    observations up to and including this session are supplied.
    """
    if not observations_up_to_and_including_session:
        raise PublicationReplayError("At least one observation is required to replay published state.")

    result = _invoke_bridge(observations_up_to_and_including_session, mic_v2_python, mic_v2_cwd, timeout_seconds)

    return PublishedState(
        market_context=result["market_context"],
        market_opinion=result["market_opinion"],
        context_stability=result["context_stability"],
        calibration=result["calibration"],
        governance=result["governance"],
        lifecycle=result["lifecycle"],
        contract=result["contract"],
        certification_status=result.get("certification_status"),
        certification_deterministic=result.get("certification_deterministic"),
        publication_ids={
            "context_id": result["context_id"],
            "opinion_id": result["opinion_id"],
            "stability_id": result["stability_id"],
            "calibration_id": result["calibration_id"],
            "governance_id": result["governance_id"],
            "lifecycle_id": result["lifecycle_id"],
            "contract_record_id": result["contract_record_id"],
            "certification_id": result.get("certification_id"),
        },
        candle_count=result["candle_count"],
        stability_reasoning_summary=result.get("stability_reasoning_summary"),
        stability_transition_count=result.get("stability_transition_count"),
        stability_persistence_length=result.get("stability_persistence_length"),
        stability_dimension_agreement=result.get("stability_dimension_agreement"),
        stability_confidence_variance=result.get("stability_confidence_variance"),
        stability_context_lifetime=result.get("stability_context_lifetime"),
        context_volatility=result.get("context_volatility"),
        context_liquidity=result.get("context_liquidity"),
        context_regime=result.get("context_regime"),
        context_conviction=result.get("context_conviction"),
        context_stability_dimension=result.get("context_stability_dimension"),
        transition_events=tuple(result.get("transition_events") or ()),
    )


def replay_corpus_published_states(
    observations: Sequence[Dict],
    mic_v2_python: str = DEFAULT_MIC_V2_PYTHON,
    mic_v2_cwd: str = DEFAULT_MIC_V2_CWD,
    timeout_seconds: float = 60.0,
) -> List[Tuple[Dict, PublishedState]]:
    """Replay an entire corpus, session by session, in the order
    given (the caller -- Series 59's corpus builder -- has already
    restored chronology), growing the candle history one session at a
    time. Returns `[(observation, PublishedState), ...]` in corpus
    order. An empty `observations` sequence returns an empty list --
    never an error, never a fabricated record.
    """
    results = []
    history: List[Dict] = []
    for obs in observations:
        history.append(obs)
        state = replay_published_state_for_session(history, mic_v2_python, mic_v2_cwd, timeout_seconds)
        results.append((obs, state))
    return results
