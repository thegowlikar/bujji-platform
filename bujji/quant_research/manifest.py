"""Dataset identity and phase classification for offline quant research.

OFFLINE ONLY. Nothing in `bujji.quant_research` may appear in the reachability
closure of a declared entrypoint, and a test asserts it. It reads durable
artifacts and writes research outputs; it never feeds selection, ranking,
sizing, risk or execution.

WHY A MANIFEST AT ALL. A research result is a claim about a dataset. If the
dataset cannot be named and hashed, the claim cannot be checked, repeated or
refuted -- and an unreproducible backtest is worse than none, because it still
argues. Every dataset here carries its source hashes, universe identity,
session date and phase classification, and changing the bytes without changing
the manifest is a refusal.

PHASES ARE NOT ALL EVIDENCE. The 2026-08-24 session produced valid phases
alongside an INVALID one -- full sustained ran through its deadline and
accumulated 4h46m of post-close silence -- and unmeasured ones. Research that
silently mixes them would compute a throughput or volatility figure from
market data and silence averaged together. The classification is therefore
part of dataset identity, not a comment.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

# Phase classifications, carried from the Gate 1 evidence ledger. These are
# operator-locked judgements about a session, not something research re-decides.
PHASE_VALID = "VALID"
PHASE_INVALID = "INVALID"
PHASE_UNMEASURED = "UNMEASURED"

# Refusals. A dataset that cannot establish its own identity is not usable.
REFUSE_SOURCE_MISSING = "SOURCE_MISSING"
REFUSE_HASH_MISMATCH = "HASH_MISMATCH"
REFUSE_NO_PHASE_CLASSIFICATION = "NO_PHASE_CLASSIFICATION"
REFUSE_INVALID_PHASE_INCLUDED = "INVALID_PHASE_INCLUDED"
REFUSE_UNMEASURED_PHASE_INCLUDED = "UNMEASURED_PHASE_INCLUDED"
REFUSE_NO_UNIVERSE_IDENTITY = "NO_UNIVERSE_IDENTITY"
REFUSE_EMPTY = "EMPTY_DATASET"


def sha256_file(path: Path, *, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


@dataclass(frozen=True)
class PhaseWindow:
    """One classified slice of a session.

    `start_ts`/`end_ts` are receiver wall-clock seconds, the same clock the
    corpus records. A window with no bounds covers the whole source and is only
    usable when the whole source shares one classification.
    """
    name: str
    classification: str
    start_ts: Optional[float] = None
    end_ts: Optional[float] = None
    note: str = ""

    def contains(self, ts: Optional[float]) -> bool:
        if ts is None:
            return False
        if self.start_ts is not None and ts < self.start_ts:
            return False
        if self.end_ts is not None and ts > self.end_ts:
            return False
        return True


@dataclass
class DatasetManifest:
    """Immutable identity of a research dataset."""
    dataset_id: str
    session_date: str
    sources: Dict[str, str] = field(default_factory=dict)        # path -> sha256
    universe_id: Optional[str] = None
    universe_sha256: Optional[str] = None
    symbols_sha256: Optional[str] = None
    release_id: Optional[str] = None
    field_availability: Dict[str, Any] = field(default_factory=dict)
    phases: List[PhaseWindow] = field(default_factory=list)
    included_classifications: Tuple[str, ...] = (PHASE_VALID,)
    notes: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "dataset_id": self.dataset_id, "session_date": self.session_date,
            "sources": self.sources, "universe_id": self.universe_id,
            "universe_sha256": self.universe_sha256,
            "symbols_sha256": self.symbols_sha256,
            "release_id": self.release_id,
            "field_availability": self.field_availability,
            "phases": [{"name": p.name, "classification": p.classification,
                        "start_ts": p.start_ts, "end_ts": p.end_ts,
                        "note": p.note} for p in self.phases],
            "included_classifications": list(self.included_classifications),
            "notes": self.notes,
        }

    def fingerprint(self) -> str:
        """Identity of the dataset AS DECLARED.

        Deliberately includes the phase classification and the inclusion rule:
        the same bytes filtered differently are a different dataset, and a
        result carried between them is not the same result.
        """
        blob = json.dumps(self.as_dict(), sort_keys=True).encode("utf-8")
        return hashlib.sha256(blob).hexdigest()


def verify_manifest(m: DatasetManifest, *, recompute: bool = True) -> List[Dict[str, str]]:
    """Refuse a dataset that cannot prove what it is. Empty list means usable."""
    refusals: List[Dict[str, str]] = []

    def refuse(code, detail):
        refusals.append({"code": code, "detail": detail})

    if not m.sources:
        refuse(REFUSE_EMPTY, "the manifest names no source files")
    for path, declared in m.sources.items():
        p = Path(path)
        if not p.is_file():
            refuse(REFUSE_SOURCE_MISSING, f"{path} does not exist")
            continue
        if recompute:
            actual = sha256_file(p)
            if actual != declared:
                refuse(REFUSE_HASH_MISMATCH,
                       f"{path}: manifest says {declared[:16]}..., file is "
                       f"{actual[:16]}... -- the data changed without the "
                       f"manifest changing, so every result computed from it "
                       f"is about a dataset that no longer exists")

    if not m.phases:
        refuse(REFUSE_NO_PHASE_CLASSIFICATION,
               "no phase classification: research cannot tell measured data "
               "from post-close silence")
    for p in m.phases:
        if p.classification not in (PHASE_VALID, PHASE_INVALID, PHASE_UNMEASURED):
            refuse(REFUSE_NO_PHASE_CLASSIFICATION,
                   f"phase {p.name!r} has classification {p.classification!r}")

    if PHASE_INVALID in m.included_classifications:
        refuse(REFUSE_INVALID_PHASE_INCLUDED,
               "a phase classified INVALID is included. Invalid means the "
               "measurement is known not to describe the market -- including "
               "it does not add data, it adds error")
    if PHASE_UNMEASURED in m.included_classifications:
        refuse(REFUSE_UNMEASURED_PHASE_INCLUDED,
               "a phase classified UNMEASURED is included; there is nothing "
               "in it to measure")

    if not (m.universe_id or m.universe_sha256):
        refuse(REFUSE_NO_UNIVERSE_IDENTITY,
               "no universe identity: which instruments this dataset covers "
               "cannot be established")
    return refusals
