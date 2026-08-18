"""Certification gate — Phase 17E.

Answers exactly one question: is this (access_method, instrument_type)
pair CERTIFIED_AVAILABLE right now, according to the real certification
artifacts on disk?

Reads `data_certification/*.json` -- the artifacts produced by
`scripts/verify_fo_access.py`'s real, dated certification runs. No
network, no broker, no token. This module never certifies anything
itself; it only reads what a certification run already recorded.

FAIL CLOSED, ALWAYS. A missing artifact, an unreadable artifact, an
unmapped instrument type, or any status other than CERTIFIED_AVAILABLE
results in a state that denies the write. An absent certification is
never an implicit pass -- that inversion is precisely the failure mode
Phase 17A's original false conclusion came from.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Optional, Tuple, Union

from . import taxonomy

# Maps a Layer 0 instrument type onto the `instrument` key recorded
# inside the certification artifacts themselves. Explicit and closed:
# an instrument type absent from this map is CERTIFICATION_MISSING, not
# silently assumed certified.
#
# INSTRUMENT_INDEX now maps to INDIA_VIX (Phase 17G). Prior to this, INDEX
# was deliberately absent from this map -- Phase 17A.5 never produced a
# VIX certification artifact, so VIX genuinely was not certified for
# Layer 0 writes. `scripts/certify_vix_access.py` closes that gap by
# producing a real, dated artifact under this exact key -- until that
# script is actually run (market hours, operator action), this entry
# still correctly resolves to CERTIFICATION_MISSING, because `_load()`
# only finds a record if the artifact file exists on disk. Adding the
# key does not fabricate a certification; it only lets a real one, once
# produced, be recognized.
#
# NOTE: NSE:NIFTY50-INDEX (spot) is ALSO an INDEX instrument type but is
# certified under INSTRUMENT_SPOT, not INSTRUMENT_INDEX -- the two
# taxonomy constants distinguish "the underlying we trade derivatives on"
# (SPOT) from "a standalone index instrument" (INDEX, e.g. VIX). This
# mapping only ever applies to the latter.
INSTRUMENT_TYPE_TO_CERT_KEY: Dict[str, str] = {
    taxonomy.INSTRUMENT_SPOT: "NIFTY_SPOT",
    taxonomy.INSTRUMENT_FUTURE: "NIFTY_FUTURES",
    taxonomy.INSTRUMENT_OPTION: "NIFTY_OPTION_CE",
    taxonomy.INSTRUMENT_INDEX: "INDIA_VIX",
}


class CertificationGate:
    """Reads certification artifacts from a directory.

    Status is read LIVE per query by default (`cache=False`) rather than
    snapshotted at construction: if a re-certification demotes a source
    mid-session, Layer 0 writes for it must stop immediately, without a
    restart or a code change (Phase 17D Part 2's write-gate rule).
    """

    def __init__(self, cert_dir: Union[str, Path], cache: bool = False) -> None:
        self._dir = Path(cert_dir)
        self._cache_enabled = cache
        self._cache: Optional[Dict[Tuple[str, Optional[str]], dict]] = None

    @property
    def cert_dir(self) -> Path:
        return self._dir

    def _load(self) -> Dict[Tuple[str, Optional[str]], dict]:
        """Index every readable artifact by (instrument, access_method).

        FOUND DURING PHASE 17G.0's GATE B AUDIT: this was previously keyed
        by `instrument` alone, which silently collapsed two DISTINCT
        certification subjects into one slot whenever they shared an
        instrument but not an access method -- e.g. a REST NIFTY_SPOT
        certification and a websocket NIFTY_SPOT certification. Only the
        alphabetically-last-sorted artifact survived in the index; the
        other became invisible to every `status_for()` call even though
        its file still existed on disk. This is not a hypothetical: the
        first time a websocket certification was ever going to be
        produced (Gate B), it would have silently shadowed the existing,
        working REST spot certification, turning a previously-CERTIFIED
        write path into CERTIFICATION_MISSING purely as a side effect of
        running an unrelated certification script. `access_method` is
        part of the identity of what was certified -- "the REST path
        works" and "the websocket path works" are two different facts
        about the same instrument, exactly the conflation the MCP
        connector incident already proved dangerous once. Keying on the
        pair makes that conflation structurally impossible instead of
        depending on filename sort order.

        An unreadable or malformed artifact is skipped -- it cannot
        certify anything, and skipping it means the gate denies rather
        than crashes.
        """
        if self._cache_enabled and self._cache is not None:
            return self._cache

        by_key: Dict[Tuple[str, Optional[str]], dict] = {}
        if self._dir.is_dir():
            for path in sorted(self._dir.glob("*.json")):
                try:
                    record = json.loads(path.read_text())
                except (OSError, json.JSONDecodeError):
                    continue
                if not isinstance(record, dict):
                    continue
                instrument_key = record.get("instrument")
                if not instrument_key:
                    continue
                record = dict(record)
                record["_artifact"] = path.name
                by_key[(instrument_key, record.get("access_method"))] = record

        if self._cache_enabled:
            self._cache = by_key
        return by_key

    def status_for(
        self, access_method: str, instrument_type: str
    ) -> Tuple[str, Optional[str]]:
        """Returns (certification_status, certification_ref).

        `certification_ref` is the artifact filename plus that artifact's
        own recorded run timestamp, so any Layer 0 record's certification
        claim is independently auditable against a real, dated run rather
        than being a bare assertion.
        """
        cert_key = INSTRUMENT_TYPE_TO_CERT_KEY.get(instrument_type)
        if cert_key is None:
            return taxonomy.CERTIFICATION_MISSING, None

        # Looked up by the EXACT (instrument, access_method) pair -- a
        # certification of a different access method for the same
        # instrument simply isn't this record; no separate equality
        # check is needed once the lookup key itself encodes both,
        # eliminating the collision the old instrument-only index had.
        record = self._load().get((cert_key, access_method))
        if record is None:
            return taxonomy.CERTIFICATION_MISSING, None

        status = record.get("validation_result")
        if status not in taxonomy.ALL_CERTIFICATION_STATES:
            return taxonomy.NOT_CERTIFIED, record.get("_artifact")

        ref = record.get("_artifact")
        timestamp = record.get("timestamp")
        if ref and timestamp:
            ref = f"{ref}@{timestamp}"
        return status, ref

    def permits_write(self, access_method: str, instrument_type: str) -> bool:
        status, _ = self.status_for(access_method, instrument_type)
        return status in taxonomy.WRITE_PERMITTED_CERTIFICATION_STATES


class StaticCertificationGate(CertificationGate):
    """A gate with an explicitly supplied status, for tests and for
    deterministic replay. Never reads the filesystem -- so a validator
    test can exercise every certification branch without fabricating
    certification artifacts on disk."""

    def __init__(self, status: str, ref: Optional[str] = "static") -> None:  # noqa: D107
        super().__init__(cert_dir=".", cache=True)
        self._status = status
        self._ref = ref

    def status_for(
        self, access_method: str, instrument_type: str
    ) -> Tuple[str, Optional[str]]:
        return self._status, self._ref
