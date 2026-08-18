"""Position Lifecycle -- canonical identity, Phase 15G.

`position_id`: deterministic hash of (session_id, candidate_id,
entry_timestamp) -- NOT a broker order id, NOT dependent on symbol or
timestamp alone, NOT dependent on JSONL line position. Same three real
inputs always produce the same id (replay-safe, idempotent); a
DIFFERENT entry_timestamp for the same candidate_id is a genuinely
different position, so it correctly gets a different id -- a position
identity can never be silently reused for two real entry moments.

`leg_id`: deterministic hash of (position_id, role, option_type,
strike, expiry) -- scoped under its parent position_id, so identical
leg shapes in two different positions never collide.
"""
from __future__ import annotations

import hashlib


def position_id_for(session_id: str, candidate_id: str, entry_timestamp: str) -> str:
    return "POS-" + hashlib.md5(f"{session_id}|{candidate_id}|{entry_timestamp}".encode()).hexdigest()[:24]


def leg_id_for(position_id: str, role: str, option_type: str, strike: float, expiry: str) -> str:
    return "LEG-" + hashlib.md5(f"{position_id}|{role}|{option_type}|{strike}|{expiry}".encode()).hexdigest()[:16]
