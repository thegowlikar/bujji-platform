"""Runtime / calculation / decision identity -- Phase 16D.

THE BOUNDED IMPURE EDGE of `bujji.epistemics`. `uncertainty.py`,
`lineage.py` and `adapters.py` remain stdlib-pure; this module is
allowed exactly one impurity -- reading the deployment's own git and
config state -- and nothing else. No network, no broker, no market
data, no decision logic.

WHY IT EXISTS (audited, not assumed):

  `shadow_observatory.SessionManifest` ALREADY defines the identity-card
  shape (session_id, mode, strategy_engine, risk_engine, broker,
  code_version, config_hash, market, symbols) and its docstring is
  explicitly correct:

      "code_version/config_hash are deployment-level facts this package
       cannot know on its own (it never inspects git or config files
       itself), so a missing value stays None rather than being guessed."

  That discipline is right, and it identified the real gap: the FIELD
  exists, and NOTHING POPULATES IT. `SessionManifest(...)` is
  constructed only in tests; a repo-wide search for git inspection
  (`git rev-parse`, GitPython, `subprocess ... git`) returns ZERO hits.

  So Phase 16D does not invent an identity model. It builds the missing
  PRODUCER for a model that already exists, and adapts rather than
  replaces (`to_session_manifest_fields()` below).

REUSED, NOT REBUILT:
  * content hashing  -> `replay_engine.fingerprint_state` (the canonical
    replay owner's own deterministic SHA-256; six fingerprint
    implementations already exist and a seventh would be indefensible)
  * identity-card shape -> `shadow_observatory.SessionManifest`
  * multi-config versioning -> `mil_next`'s `active_config_versions`
    Dict[str, str] pattern
  * lineage fields -> `epistemics.lineage.Lineage`

EPISTEMIC RULE: every field here is either a REAL resolved fact or
`None`. Nothing is guessed. An unavailable git sha stays `None` and
`is_reproducible` reports False -- exactly as `SessionManifest` already
insists.
"""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from typing import Mapping, Optional, Tuple

SCHEMA_VERSION = "1.0.0"

UNKNOWN_VERSION = None          # never a placeholder string -- absence must stay absent
_GIT_TIMEOUT_S = 5


# --------------------------------------------------------------------- code
def _git(args, cwd: Optional[str]) -> Optional[str]:
    """Run one git command. Returns None on ANY failure -- a missing
    git, a non-repo directory, a timeout. Never raises, never guesses."""
    try:
        out = subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                             text=True, timeout=_GIT_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return out.stdout.strip() or None


@dataclass(frozen=True)
class CodeIdentity:
    """WHICH CODE produced a result.

    `dirty` is the field that matters most in practice: a result
    produced from an uncommitted working tree is NOT reproducible from
    the commit alone, and saying so is the difference between honest
    provenance and a comforting lie. Bujji's own working tree has been
    uncommitted for this entire programme -- so `dirty=True` will be the
    normal, correct answer, and it must be recorded rather than hidden.
    """

    commit: Optional[str] = UNKNOWN_VERSION
    short_commit: Optional[str] = UNKNOWN_VERSION
    branch: Optional[str] = UNKNOWN_VERSION
    dirty: Optional[bool] = None            # None = could not determine (NOT "clean")
    resolved: bool = False

    @property
    def code_version(self) -> Optional[str]:
        """The single string `SessionManifest.code_version` expects.
        Carries the dirty marker, because a dirty tree is a different
        artifact from its commit."""
        if not self.commit:
            return UNKNOWN_VERSION
        return f"{self.short_commit or self.commit[:12]}{'+dirty' if self.dirty else ''}"

    @property
    def is_reproducible(self) -> bool:
        """Only a clean, resolved commit can be reproduced from source."""
        return bool(self.resolved and self.commit and self.dirty is False)

    def to_dict(self) -> dict:
        return {"commit": self.commit, "short_commit": self.short_commit,
                "branch": self.branch, "dirty": self.dirty,
                "resolved": self.resolved, "code_version": self.code_version}


def resolve_code_identity(repo_root: Optional[str] = None) -> CodeIdentity:
    """Inspect git ONCE. Intended to be called at process start and
    injected thereafter -- never called from a decision path, which
    must remain a pure function of its inputs."""
    root = repo_root or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    commit = _git(["rev-parse", "HEAD"], root)
    if commit is None:
        return CodeIdentity(resolved=False)
    status = _git(["status", "--porcelain"], root)
    return CodeIdentity(
        commit=commit,
        short_commit=_git(["rev-parse", "--short=12", "HEAD"], root),
        branch=_git(["rev-parse", "--abbrev-ref", "HEAD"], root),
        dirty=(status is not None and status != ""),
        resolved=True,
    )


# ------------------------------------------------------------------- config
@dataclass(frozen=True)
class ConfigIdentity:
    """WHICH CONFIGURATION governed a result.

    `versions` follows `mil_next`'s existing `active_config_versions:
    Dict[str, str]` pattern rather than inventing a new shape -- several
    configs can be active at once and collapsing them to one string
    would lose which one changed.
    """

    config_hash: Optional[str] = UNKNOWN_VERSION
    versions: Tuple[Tuple[str, str], ...] = ()      # (name, version) -- hashable, ordered
    sources: Tuple[str, ...] = ()
    resolved: bool = False

    @property
    def is_reproducible(self) -> bool:
        return bool(self.resolved and self.config_hash)

    def to_dict(self) -> dict:
        return {"config_hash": self.config_hash, "versions": dict(self.versions),
                "sources": list(self.sources), "resolved": self.resolved}


def resolve_config_identity(payload: Optional[Mapping] = None,
                            sources: Tuple[str, ...] = (),
                            versions: Optional[Mapping[str, str]] = None) -> ConfigIdentity:
    """Hash a caller-supplied config payload. This module never READS
    config files -- the caller owns that, exactly as `SessionManifest`
    insists. A `None` payload yields an unresolved identity, never a
    fabricated hash."""
    if payload is None:
        return ConfigIdentity(sources=tuple(sources), resolved=False,
                              versions=tuple(sorted((versions or {}).items())))
    from bujji.replay_engine.engine import fingerprint_state   # REUSED, not reimplemented
    return ConfigIdentity(
        config_hash=fingerprint_state(dict(payload)),
        versions=tuple(sorted((versions or {}).items())),
        sources=tuple(sources), resolved=True,
    )


# -------------------------------------------------------------- calculation
@dataclass(frozen=True)
class CalculationIdentity:
    """WHICH FORMULA, with WHICH PARAMETERS, produced a value."""

    name: str
    calc_version: str
    formula_id: Optional[str] = None
    parameters: Tuple[Tuple[str, str], ...] = ()

    def to_dict(self) -> dict:
        return {"name": self.name, "calc_version": self.calc_version,
                "formula_id": self.formula_id, "parameters": dict(self.parameters)}


def resolve_calculation_identity(name: str, definition: str,
                                 parameters: Optional[Mapping] = None,
                                 formula_id: Optional[str] = None) -> CalculationIdentity:
    """`calc_version` is a CONTENT HASH of (definition, parameters),
    reusing `replay_engine.fingerprint_state`. A hand-maintained integer
    drifts silently the first time someone edits a formula and forgets
    to bump it -- and a silently-drifted version is worse than none,
    because two incompatible definitions then look like one series."""
    from bujji.replay_engine.engine import fingerprint_state   # REUSED
    params = dict(parameters or {})
    digest = fingerprint_state({"definition": definition, "parameters": params})
    return CalculationIdentity(
        name=name, calc_version="CV-" + digest[:16], formula_id=formula_id,
        parameters=tuple(sorted((k, str(v)) for k, v in params.items())),
    )


# ------------------------------------------------------------ runtime bundle
@dataclass(frozen=True)
class RuntimeIdentity:
    """Resolved ONCE at process start, then injected.

    Adapts onto `shadow_observatory.SessionManifest` via
    `to_session_manifest_fields()` -- that model is not replaced, it is
    finally supplied with the values it was always designed to carry.
    """

    code: CodeIdentity
    config: ConfigIdentity
    environment: str = "unknown"
    schema_version: str = SCHEMA_VERSION

    @property
    def is_fully_reproducible(self) -> bool:
        return self.code.is_reproducible and self.config.is_reproducible

    def unreproducible_reasons(self) -> Tuple[str, ...]:
        """Names exactly what prevents reproduction rather than
        reporting a bare False."""
        out = []
        if not self.code.resolved:
            out.append("code_identity_unresolved")
        elif self.code.dirty:
            out.append("working_tree_dirty")
        if not self.config.resolved:
            out.append("config_identity_unresolved")
        return tuple(out)

    def to_session_manifest_fields(self) -> dict:
        """ADAPTER: the exact `code_version`/`config_hash` fields
        `SessionManifest` declares. Never fabricates -- a missing value
        stays None, which is what that model already requires."""
        return {"code_version": self.code.code_version,
                "config_hash": self.config.config_hash}

    def to_dict(self) -> dict:
        return {"code": self.code.to_dict(), "config": self.config.to_dict(),
                "environment": self.environment, "schema_version": self.schema_version,
                "is_fully_reproducible": self.is_fully_reproducible,
                "unreproducible_reasons": list(self.unreproducible_reasons())}


def resolve_runtime_identity(config_payload: Optional[Mapping] = None,
                             config_sources: Tuple[str, ...] = (),
                             config_versions: Optional[Mapping[str, str]] = None,
                             environment: str = "unknown",
                             repo_root: Optional[str] = None) -> RuntimeIdentity:
    return RuntimeIdentity(
        code=resolve_code_identity(repo_root),
        config=resolve_config_identity(config_payload, config_sources, config_versions),
        environment=environment,
    )


# ----------------------------------------------------------- decision context
@dataclass(frozen=True)
class DecisionContext:
    """Carries `as_of` and identity INTO a decision, so no decision path
    needs to read a clock.

    This is the mechanism that removes the last wall-clock dependence
    found by the Phase 16C lineage audit
    (`shadow_trade_construction:181`'s `datetime.now()` fallback):
    the caller supplies `as_of`, and a decision made from a replayed
    record is dated by the RECORD, never by replay time.
    """

    as_of: str                                   # EVENT time -- never arrival, never wall clock
    runtime: Optional[RuntimeIdentity] = None
    experiment: Optional["ExperimentIdentity"] = None
    session_id: Optional[str] = None

    @property
    def as_of_date(self) -> str:
        """The date component, replacing the audited
        `source_cycle_id[:10] if source_cycle_id else datetime.now(...)`
        fallback with an injected, replay-stable value."""
        return self.as_of[:10]

    def to_dict(self) -> dict:
        return {"as_of": self.as_of, "session_id": self.session_id,
                "runtime": self.runtime.to_dict() if self.runtime else None,
                "experiment": self.experiment.to_dict() if self.experiment else None}


# ------------------------------------------------------------- experiment ids
@dataclass(frozen=True)
class ExperimentIdentity:
    """CONTRACT ONLY -- no research platform is built here.

    Deliberately mirrors the `run_id` semantics that already exist in
    `qualification.replay_models` and
    `trading_brain.risk_governor.margin_calibration_runner`, so a future
    research layer adopts an established convention instead of a
    competing one.

    Hierarchy: campaign (a research programme) > experiment (one
    hypothesis) > run (one execution). All optional: production
    decisions carry none of them, and that absence is meaningful --
    it distinguishes a live decision from a research artifact.
    """

    run_id: Optional[str] = None
    experiment_id: Optional[str] = None
    campaign_id: Optional[str] = None

    @property
    def is_research(self) -> bool:
        """True when this artifact belongs to an experiment rather than
        production. A research artifact must never be promoted into
        production evidence without an explicit, audited gate."""
        return any((self.run_id, self.experiment_id, self.campaign_id))

    def to_dict(self) -> dict:
        return {"run_id": self.run_id, "experiment_id": self.experiment_id,
                "campaign_id": self.campaign_id, "is_research": self.is_research}
