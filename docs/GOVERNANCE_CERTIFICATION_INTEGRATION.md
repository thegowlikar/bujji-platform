# Governance Certification Integration v1

**BUJJI Options OS v3 — Engineering Series 65**

## Root cause

`mic_v2.governance.engine.derive_governance()` has always accepted a
`certification` argument. Every prior call from
`bujji/mic_replay/publication_replay.py`'s bridge script passed
`certification=None` (the only value ever supplied), which is exactly
why `governance` resolved to `REJECTED` on almost every real session
across Campaign v2/v2.1/Sprint B. This was a wiring gap in the bridge,
not a MIC v2 defect: `mic_v2.certification.runner.run_replay_with_certification()`
already exists, already frozen, already runs the real pipeline with
real on-disk journals and certifies them.

## What changed

`_BRIDGE_SCRIPT` now calls `run_replay_with_certification(candles, <fresh temp dir>)`
first (real `CertificationReport`, temp dir cleaned up before the
subprocess exits), then passes it into
`run_replay_with_contract(candles, certification=certification_report)`.
`PublishedState` gained `certification_status`/`certification_deterministic`
(both `Optional`, default `None` — backward compatible), and
`publication_ids` gained `certification_id`. No MIC v2 file touched;
no other bridge call, no Strategy/Risk/Capital/Runtime/OI/VIX logic
touched.

## Evidence discipline preserved

Certification is genuinely computed by MIC v2's own frozen pipeline —
never fabricated. If the subprocess response omits certification keys
(e.g. a stale response shape), `PublishedState` fields default to
`None`, never guessed.
