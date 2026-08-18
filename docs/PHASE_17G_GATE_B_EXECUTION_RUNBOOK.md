# Phase 17G — Gate B Execution Runbook

**Status: EXECUTION PROCEDURE. No code changes authorized by this
document. No architecture expansion.**

**Objective:** convert broker-capability assumptions into verified
evidence. Nothing here builds `MarketStateSnapshot`, zones,
support/resistance, regime detection, strategy selection, or reasoning of
any kind. Gate B proves **Reality** only:

```
Reality → Memory → Understanding → Intelligence → Strategy
```

All four scripts below are read-only, market-hours gated, and have never
been executed as of this document. All prerequisites (certification
collision fix, artifact date-stamping) are confirmed landed — see
`docs/PHASE_17G_GATE_B_REALITY_CERTIFICATION_REVIEW.md` Part 2 — and full
regression is green (5,395 passed, 0 failed).

---

## Task 1 — Readiness audit (verified against the actual code, this session)

### 1.1 `scripts/certify_vix_access.py`

| | |
|---|---|
| **Exact command** | `cd /opt/bujji/app && set -a && source /tmp/local_fyers.env && set +a && PYTHONPATH=/opt/bujji/app python scripts/certify_vix_access.py` |
| **Prerequisites** | `config/config.yaml` broker section populated; `FYERS_ACCESS_TOKEN`/`app_id` present in the sourced env |
| **Market-hours gate** | Aborts, writes **no artifact**, exits 1, if `now` (IST) is outside `09:15–15:30` Mon–Fri. No override flag exists. |
| **Auth requirement** | A live, valid FYERS token via `AppConfig.load("config/config.yaml")` → `cfg.broker`; no separate token-refresh step inside the script — refreshing is the operator's own step (Task 0 below) |
| **Artifact path** | `data_certification/` |
| **Artifact filename** | `fyers_india_vix_certification_YYYYMMDD.json` (date-stamped from the cert's own `timestamp` field, fixed this session) |
| **Certifies** | Whether `NSE:INDIAVIX-INDEX` is reachable via `ltp`+`historical` over `direct_sdk_fyers_broker_py`, with symbol-echo and candle-integrity checks |
| **Does NOT certify** | VIX over websocket (REST only); VIX depth/volume/OI (structurally `NOT_APPLICABLE` for an index, correctly recorded as such, not tested as a gap) |

### 1.2 `scripts/discover_option_chain_premium_fields.py`

| | |
|---|---|
| **Exact command** | `... && PYTHONPATH=/opt/bujji/app python scripts/discover_option_chain_premium_fields.py` (optionally `--strike-count N`, default 5) |
| **Prerequisites** | Same as 1.1 |
| **Market-hours gate** | Same abort behavior, exit 1, no artifact |
| **Auth requirement** | Same as 1.1 |
| **Artifact path** | `data_certification/` |
| **Artifact filename** | `fyers_option_chain_discovery_YYYYMMDD.json` |
| **Certifies (discovers, not certification-gate-shaped)** | The real, raw, unmodified `optionchain` response across a real multi-strike chain — every raw key seen, per-row example values, a CE-vs-PE key comparison, and which `OptionObservation` fields are separately needed (never claiming a mapping) |
| **Does NOT certify** | Any FYERS-key → `OptionObservation`-field mapping (a human decision from the raw output); chain behavior over time (single point sample). **Not read by `CertificationGate`** — this artifact has no `instrument`/`validation_result` shape |

### 1.3 `scripts/certify_websocket_access.py`

| | |
|---|---|
| **Exact command** | `... && PYTHONPATH=/opt/bujji/app python scripts/certify_websocket_access.py` |
| **Prerequisites** | Same as 1.1. **Longest-running script: ~130s wall clock** (30s connect timeout budget + 120s listen window) |
| **Market-hours gate** | Same abort behavior, exit 1, no artifact |
| **Auth requirement** | Same as 1.1; additionally requires the websocket handshake itself to succeed (`connected.wait(timeout=30)`) |
| **Artifact path** | `data_certification/` |
| **Artifact filename** | `fyers_websocket_certification_YYYYMMDD.json` (date-stamped, fixed this session) |
| **Certifies** | Spot-only (`NSE:NIFTY50-INDEX`) websocket transport connectivity, symbol echo, tick field census (presence/null per `FIELDS_OF_INTEREST`), event-timestamp availability/validity, and — the script's most consequential check — full-mode (`litemode=False`) `ltp` scaling vs. the certified REST reference (catches an order-of-magnitude mismatch) |
| **Does NOT certify** | Futures/option websocket ticks (spot-only scope); **reconnect behavior** (`reconnect=False` explicitly — a single clean window, corrected in this session's prior audit after an earlier inaccurate claim); what production `FyersTickFeed` actually receives (it runs `litemode=True`, this script runs `litemode=False` — disclosed in every artifact's own `limitations` list); depth over websocket |

### 1.4 `scripts/discover_depth_response_shape.py`

| | |
|---|---|
| **Exact command** | `... && PYTHONPATH=/opt/bujji/app python scripts/discover_depth_response_shape.py --option-symbol NSE:<REAL_SYMBOL>` |
| **Prerequisites** | Same as 1.1. **`--option-symbol` is required for the option leg and is NOT auto-constructed** — no live-verified option-symbol builder exists in this codebase; source a real, currently-tradable symbol from step 2's output first (dependency, see Task 2) |
| **Market-hours gate** | Same abort behavior, exit 1, no artifact |
| **Auth requirement** | Same as 1.1 |
| **Artifact path** | `data_certification/` |
| **Artifact filename** | `fyers_depth_discovery_YYYYMMDD.json` |
| **Certifies (discovers)** | Raw, unmodified `depth` response shape for a futures symbol and (if supplied) one option contract; two polls 5s apart (`SECOND_POLL_DELAY_SECONDS`) per instrument, with a labeled-as-non-proof snapshot-vs-incremental signal |
| **Does NOT certify** | Any interpretation of `bids`/`asks` shape; session-long behavior (point sample only); anything about options if `--option-symbol` is omitted (futures leg only in that case). **Not read by `CertificationGate`** — same non-certification shape as 1.2 |

### 1.5 No execution blockers found

All four scripts compile, all four are covered by passing tests
(pure-logic tests only — none require live network), and the one real
defect found during the prior audit (`CertificationGate` instrument/
access_method collision) is fixed and verified. **No code changes are
required before execution.**

---

## Task 2 — Execution runbook

### Pre-flight

1. **VPS location:** `root@139.59.76.137:/opt/bujji/app`
2. **Environment activation:**
   ```bash
   cd /opt/bujji/app
   set -a && source /tmp/local_fyers.env && set +a
   ```
3. **Token verification:**
   ```bash
   echo $FYERS_ACCESS_TOKEN | wc -c
   ```
   Confirms a non-empty token is loaded. This does not confirm the token
   is *valid* — an expired/invalid token surfaces as an `AuthenticationError`
   at the first live call each script makes, handled explicitly by every
   script (see Task 4).
4. **Market-hours requirement:** all four scripts require **09:15–15:30
   IST, Monday–Friday**. Any run outside that window aborts with no
   artifact, by design (a zero-observation result outside market hours is
   uninterpretable, not a finding).
5. **Artifact directory check:**
   ```bash
   ls -la data_certification/
   ```
   Confirms the three pre-existing 2026-08-12 REST certifications
   (`fyers_nifty_spot_certification.json`,
   `fyers_nifty_future_certification.json`,
   `fyers_option_chain_certification.json`) are present and undisturbed
   before adding anything new.

### Execution order

```
1. VIX certification
2. Option chain premium discovery
3. Websocket certification
4. Depth discovery
```

**Why this order:**

- **VIX first** — fastest (a few REST calls), fully independent of every
  other step, run while the token is freshest.
- **Option chain discovery second** — its output is a **real dependency**
  for step 4: `discover_depth_response_shape.py --option-symbol` requires
  a real, currently-tradable option symbol, and this codebase has no
  live-verified symbol-construction helper (by design — guessing one
  would repeat the exact mistake these discovery scripts exist to
  prevent). Running this step second, before depth discovery, means a
  real symbol is already in hand rather than sourced separately.
- **Websocket certification third** — the longest-running step (~130s),
  placed where it blocks nothing downstream (step 4 depends only on step
  2's output, not on step 3 completing).
- **Depth discovery last** — consumes step 2's real option symbol.

For each step:

```bash
PYTHONPATH=/opt/bujji/app python scripts/<script>.py [args]
ls -lh data_certification/
```

Copy each new artifact to durable storage immediately after it's written
(off the VPS, or into version control if the operator chooses) — these
are the first real observations of FYERS's actual behavior this project
has captured under the current certification framework, per the
operator's own framing: **first pieces of Bujji's actual market memory.**

---

## Task 3 — Evidence preservation rules

- **Raw artifacts are immutable evidence.** Once written, a
  `data_certification/*.json` file is never edited by hand, ever, for any
  reason — including to "correct" a value that looks wrong. A wrong-looking
  value is itself a finding to investigate with a fresh run, never a typo
  to fix in place.
- **No normalization during discovery.** The two `discover_*.py` scripts
  write the raw FYERS response byte-for-byte (as parsed JSON) — this rule
  is already enforced by their own code (verified in Task 1), not merely
  a runbook convention.
- **No schema changes based on assumptions.** A field seen in a raw
  capture does not, by itself, authorize adding it to
  `taxonomy.REQUIRED_PAYLOAD_FIELDS`, `OptionObservation`, or any other
  Layer 0/1 contract. That is a separate, explicit decision (Task 5's
  decision tree), made after review, never automatically from a capture.
- **Every capability claim must point to an artifact.** A statement like
  "FYERS provides futures OI" must cite the specific dated file and field
  it came from — never asserted from memory of a prior session, a
  docstring, or a test fixture.

---

## Task 4 — Failure handling

**If a script fails: do not continue by guessing.** Stop, and record the
failure using this exact structure before deciding anything else:

```
script:            <exact script name>
timestamp:         <real IST timestamp of the failure>
failure_reason:    <verbatim error message / exit behavior>
category:          one of:
                     - AUTHENTICATION       (token invalid/expired/missing)
                     - BROKER_LIMITATION    (a real, observed FYERS
                                             constraint -- e.g. a
                                             genuinely empty/error
                                             response, not a bug here)
                     - MARKET_HOURS         (ran outside 09:15-15:30 IST
                                             Mon-Fri -- the script's own
                                             gate correctly aborted)
                     - CODE_DEFECT          (a real bug in the script
                                             itself -- distinguish from
                                             BROKER_LIMITATION by
                                             checking whether the SAME
                                             call succeeds via a
                                             different, already-certified
                                             path)
                     - UNKNOWN              (insufficient information to
                                             classify -- do not force a
                                             guess into one of the above
                                             four; UNKNOWN is an honest
                                             answer)
```

Each script's own exit code already discriminates the common cases:

| Script | Exit 1 | Exit 2 | Exit 3 |
|---|---|---|---|
| `certify_vix_access.py` | Market hours / config error | — | — |
| `certify_websocket_access.py` | Market hours / no token in env | — | — |
| `discover_depth_response_shape.py` | Market hours | Field-mapping-unverified (`--live` refusal; N/A to discovery-only invocation) | Auth failure (token invalid) |
| `discover_option_chain_premium_fields.py` | Market hours | `get_option_chain_raw()` raised | Auth failure (token invalid) |

A `CODE_DEFECT` classification is the only category that would justify a
code change under this phase's constraints ("do not modify unless a
genuine defect blocks evidence collection") — and even then, the fix
should be as narrow as the two already made this session (certification
date-stamping, the collision fix), verified by new tests, with full
regression re-confirmed before re-attempting the run.

---

## Task 5 — Post-run review

`docs/PHASE_17G_GATE_B_REALITY_CERTIFICATION_REVIEW.md` **already exists**
(created and updated this session) and already defines exactly this
four-state framework in its Part 3:

- **CERTIFIED** — directly observed from a real FYERS raw response.
- **UNKNOWN** — not observed yet.
- **UNAVAILABLE** — verified impossible from this broker/API (e.g. trade
  prints, aggressor side, true order flow, dealer positioning — pinned in
  that document's §3.3 and explicitly not expected to change after Gate B
  executes, since none of the four scripts are order-flow products).
- **NEEDS CAPTURE-FORWARD** — possible only after continuous collection
  begins (this document's own terminology matches; the task description
  above uses `CAPTURE_FORWARD` as an equivalent label for the same state).

That document's **Part 6 ("Live execution results")** is the explicit,
currently-empty placeholder to fill in with real values immediately after
this runbook's four steps complete — this runbook does not duplicate that
structure, it feeds it.

---

## Architectural boundary (restated, binding)

Gate B proves **Reality** only. Explicitly out of scope until Gate B's
evidence review (Part 6 of the review document) is filled in and reviewed:

- `MarketStateSnapshot`
- demand/supply zones
- support/resistance intelligence
- regime detection
- strategy selection
- AI reasoning / intelligence synthesis of any kind

```
Reality → Memory → Understanding → Intelligence → Strategy
```

Nothing in this runbook authorizes a schema change, a new field, a
fabricated FYERS field, or any of the above. It is a procedure for
running four already-built, already-tested, read-only scripts and
recording what actually happens.
