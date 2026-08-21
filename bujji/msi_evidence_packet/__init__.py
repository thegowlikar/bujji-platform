"""bujji.msi_evidence_packet -- Evidence Packet System (EPS), Series 101.

The permanent laboratory notebook layer beneath the Market Learning
Engine (Series 100). An Evidence Packet contains ONLY facts,
measurements, and references -- never a conclusion, recommendation, or
threshold change. Immutable once created (packet_id is a real content
hash; a genuine correction always produces a new id, never overwrites
the old one). NEVER imported by, and NEVER imports from, any Production
decision/execution/broker module -- same isolation discipline as Series
100, verified by tests/test_eps_isolation.py.

See docs/EVIDENCE_PACKET_SYSTEM_ARCHITECTURE.md for the full specification.
"""
from __future__ import annotations
