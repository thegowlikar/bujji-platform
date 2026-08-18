# Phase 17I.1 — First Real Observation Storage Audit

**Status: AUDIT ONLY. No code. No new schemas. No invented storage.**

Verified directly against `bujji/market_reality/store.py`,
`bujji/state_persistence/store.py`, `tests/test_market_reality_store.py`,
and the one real caller, `scripts/run_futures_depth_poller.py`, all read
in full on the VPS for this audit — nothing below is inferred from
names alone.

---

## 1. Where is `RawObservation` supposed to be persisted?

Two append-only JSONL files inside a single Layer 0 directory, created by
`RawObservationStore.__init__`:

- `raw_observations.jsonl` — accepted observations (`ACCEPTED_FILENAME`)
- `rejected_observations.jsonl` — failed-validation observations
  (`REJECTED_FILENAME`)

The directory itself is a constructor argument, not hardcoded inside
`store.py`. The one real (non-test) caller,
`run_futures_depth_poller.py:219`, passes `REPO_ROOT / "layer0_data"` —
**this is the real, already-established convention**, not a name I
invented in the prior audit. Both files live in that same directory.

## 2. Is there an existing repository/storage abstraction?

Yes — **`bujji.state_persistence.store.EventStore`**, reused unmodified.
`RawObservationStore` is explicitly documented in its own module
docstring as adding **no parallel persistence system**: it constructs
two `EventStore` instances (one per outcome) and routes records into
them. `EventStore` itself is the actual storage primitive: one JSONL
file, append-only, each write is a single `open(path, "a")` +
`f.write(json_line)` + `f.flush()` + `os.fsync()` — atomic per POSIX
`PIPE_BUF` guarantees, so a crash mid-write can only truncate the last
line, never corrupt an earlier one. `read_events()` silently skips a
torn trailing line rather than raising.

This is the same `EventStore` that already durably backs
`RegimeMemoryState`, `PaperBroker`, Observation Memory, and Outcome
Memory across five earlier phases — it is a proven, shared abstraction,
not something built for Layer 0.

## 3. Are JSON certification artifacts the intended pattern, or only discovery artifacts?

**Only discovery/certification, not observation storage — confirmed by
direct code comparison, not assumption.**

- Certification/discovery scripts (`certify_vix_access.py` line 89,
  `discover_*` scripts) call `path.write_text(json.dumps(cert, indent=2,
  default=str))` — **one full JSON object, one file, overwritten/replaced
  per run.** This fits a certification result: a single point-in-time
  verdict about broker capability.
- The observation store never does this. It appends one JSON *line* per
  record to a file that is never rewritten or truncated. This fits an
  ever-growing, replayable log of individual facts.

These are two deliberately different persistence shapes for two
different kinds of truth: certification is "what did we determine, as
of this run" (replaceable); observations are "what did we see, as an
immutable sequence" (append-only, replay-safe). A first observation
collector must use the **JSONL/`RawObservationStore` pattern**, not the
certification JSON-dump pattern.

## 4. Does `market_reality` have a writer already?

Yes — `RawObservationStore.append(raw, now)` is a complete, working
writer: certification-gates the record via `CertificationGate.status_for()`,
validates via `validator.validate()`, routes to accepted or rejected,
stamps the resolved `certification_ref` onto the stored record, and
deduplicates by `observation_id` (idempotent no-op on a repeat, not an
error). Nothing further needs to be written for a single spot
observation to be durably and correctly stored.

## 5. Smallest correct persistence implementation for the first observation

No new persistence code at all. The full sequence, using only what
exists:

```
gate  = CertificationGate(str(REPO_ROOT / "data_certification"))
store = RawObservationStore(str(REPO_ROOT / "layer0_data"), gate,
                             session_id="<new-collector-name>")
raw   = build_raw_observation(
            kind=taxonomy.KIND_QUOTE,
            instrument="NSE:NIFTY50-INDEX",
            instrument_type=taxonomy.INSTRUMENT_SPOT,
            payload={"ltp": <real value from get_spot()>},
            source="fyers",
            access_method="direct_sdk_fyers_broker_py",
            capture_timestamp=<now, injected by caller>,
            event_timestamp=None,  # unless raw response timestamp confirmed, per 17I §Blockers
            certification_status=<from gate>,
            identity_fields={},
        )
result = store.append(raw, now=<now>)
```

This is not new implementation — it is exactly the shape already proven
by `test_market_reality_store.py`'s own `_obs()` fixture default (a
`KIND_QUOTE`/`INSTRUMENT_SPOT`/`{"ltp": ...}` observation), and exactly
the same three-line construction pattern the depth poller already uses
in its `live` branch. The only genuinely new artifact is the thin script
wrapper that calls `get_spot()` and passes its value in.

## 6. Existing tests that define expected storage behavior

`tests/test_market_reality_store.py` is the authoritative contract, and
it already covers the exact spot-quote shape:

- `test_accepted_observation_is_stored_and_retrievable_unchanged` —
  default fixture IS a spot quote; asserts the stored payload round-trips
  unchanged (`payload["observation"]["value"]["payload"] == {"ltp": 24325.8}`).
- `test_identical_observation_is_an_idempotent_noop` — confirms a
  re-sent identical observation (e.g. a retried `get_spot()` call) is a
  silent no-op, not a duplicate row or a rejection.
- (Also present, not spot-specific but relevant to the same writer:
  depth/OI storability, restart-survival of the seen-id set, fail-closed
  behavior when certification is not `CERTIFIED_AVAILABLE`.)

No test currently exercises `RawObservationStore` against a *live*
broker call — every existing test constructs `RawObservation` from a
literal fixture value via `_obs()`/`build_raw_observation()` directly.
This is consistent with the Phase 17I finding: the pipeline is fully
tested end-to-end, just never yet driven by a real `get_spot()` return
value.

---

## Current persistence reality

A complete, tested, already-in-production-pattern (via the depth
poller's `live` branch) append-only JSONL writer exists and needs no
changes. Certification artifacts are a structurally separate,
intentionally different pattern and must not be reused for observation
storage. No `layer0_data/` directory exists yet on disk — its absence is
expected; `RawObservationStore.__init__` creates it on first
construction, no setup step required.

## Recommended first-write path

A new, small, market-hours-gated script mirrors
`run_futures_depth_poller.py`'s own `live` branch exactly:
`CertificationGate(data_certification/)` →
`RawObservationStore(layer0_data/, gate, session_id=<new-name>)` →
`get_spot()` → `build_raw_observation()` → `store.append()`. Same
directory (`layer0_data/`), same certification directory
(`data_certification/`) — a shared Layer 0 store across collectors, not
a new one per collector; `session_id` is the only thing that
distinguishes this collector's writes.

## Files/classes to reuse

- `bujji.market_reality.store.RawObservationStore` (unmodified)
- `bujji.market_reality.certification.CertificationGate` (unmodified)
- `bujji.market_reality.capture.build_raw_observation()` (unmodified)
- `bujji.market_reality.taxonomy` — `KIND_QUOTE`, `INSTRUMENT_SPOT`
  (unmodified)
- `bujji.state_persistence.store.EventStore` — transitively reused,
  never touched directly
- `bujji.broker.fyers.FyersBroker.get_spot()` (unmodified)
- `run_futures_depth_poller.py`'s market-hours-gate / broker-connect /
  `live`-branch structure — as a pattern to mirror, not a file to edit

## Files that should not be touched

- `bujji/market_reality/store.py`, `certification.py`, `capture.py`,
  `taxonomy.py`, `models.py` — the writer, gate, builder, and schema are
  all already correct for this case.
- `bujji/state_persistence/store.py`/`models.py` — the underlying
  durability primitive; already proven across five phases, no reason to
  touch it for a new caller.
- `scripts/run_futures_depth_poller.py` — do not add spot logic into
  this script; it is entangled with the still-unresolved depth
  field-mapping decision. Mirror its shape in a new file, don't extend it.
- Any certification JSON artifact — read-only inputs to `CertificationGate`,
  never written to by an observation collector.

## Remaining blockers

**None for persistence.** The write path is complete, tested, and
already proven in production shape by the depth poller's dormant `live`
branch. The only carried-over open item (from the Phase 17I audit, not
new to this one) is whether the real spot `ltp` REST response carries an
`event_timestamp`-worthy field beyond `lp` — this affects what value
goes into `build_raw_observation(event_timestamp=...)`, not whether or
where storage happens. Recommend resolving it by logging the raw
response once during the new collector's first real run, same as noted
in the Phase 17I audit.
