# Phase 16D — Scientific Reproducibility Layer

**Regression: 5,167 passed / 0 failed** (5,146 + 21). No frozen architecture touched.

---

## THE HEADLINE

The reuse audit changed what this phase turned out to be.

> **The identity *model* already existed. What was missing was a *producer*.**

`shadow_observatory.SessionManifest` has declared `code_version` and `config_hash` all along, with an explicitly correct docstring:

> *"code_version/config_hash are deployment-level facts this package cannot know on its own (it never inspects git or config files itself), so a missing value stays None rather than being guessed."*

That discipline is right — and it pinpointed the real gap. A repo-wide search for git inspection (`git rev-parse`, GitPython, `subprocess … git`) returned **zero hits**, and `SessionManifest(…)` is constructed **only in tests**.

So Phase 16D did not invent an identity model. It built the missing producer for one that already existed.

---

## 1. Existing identity mechanisms found

| Mechanism | Files | Verdict |
|---|--:|---|
| `schema_version` / `SCHEMA_VERSION` | 172 / 153 | **STRUCTURE version only** — not calculation identity |
| `SessionManifest` (code_version, config_hash, strategy_engine, risk_engine, broker, market) | 1 | ✅ **the identity-card shape — REUSED** |
| `replay_engine.fingerprint_state()` | 1 | ✅ **canonical deterministic SHA-256 — REUSED** |
| Other fingerprint implementations | **5 more** | ⚠️ six total already exist — a seventh would be indefensible |
| `mil_next.active_config_versions: Dict[str,str]` | 1 | ✅ **multi-config pattern — REUSED** |
| `runtime_safety.RECOGNIZED_CONFIG_VERSIONS` | 1 | config allowlist — left untouched |
| `run_id` (qualification, margin calibration) | 4 | ✅ **convention — MIRRORED** |
| `session_id` | 78 | already pervasive |
| **git / commit identity** | **0** | ❌ **the actual gap** |
| `experiment_id` / `campaign_id` | **0** | ❌ genuinely absent |

---

## 2. What was reused

| Reused | Instead of |
|---|---|
| `replay_engine.fingerprint_state` for **all** hashing | adding a 7th fingerprint implementation |
| `SessionManifest`'s field shape via `to_session_manifest_fields()` | a competing identity card |
| `mil_next`'s `Dict[str, str]` config-version pattern | collapsing configs to one string |
| `run_id` naming from qualification/margin-calibration | inventing new experiment vocabulary |
| `epistemics.Lineage`'s existing `code_version`/`config_version`/`calc_version` fields | new lineage fields |

A test asserts `identity.py` contains **no `hashlib`** — hashing must come from the canonical owner.

## 3. What was added

`bujji/epistemics/identity.py` — the **bounded impure edge** of an otherwise stdlib-pure package.

| Capability | Type | Notes |
|---|---|---|
| **Calculation identity** | `CalculationIdentity` | `calc_version` = content hash of (definition, parameters). Hand-maintained integers drift silently the first time a formula is edited — and a silently-drifted version is worse than none, because two incompatible definitions then look like one series. |
| **Code identity** | `CodeIdentity` | commit, short_commit, branch, **dirty**, `resolved`. `dirty=None` means *undetermined* and is never read as clean. |
| **Configuration identity** | `ConfigIdentity` | content hash + `Dict[str,str]` versions + sources. Never reads config files itself — the caller owns that, as `SessionManifest` insists. |
| **Timestamp discipline** | `DecisionContext` | carries injected `as_of`; `as_of_date` replaces the audited `datetime.now()` fallback. |
| **Experiment identity** | `ExperimentIdentity` | **contract only** — campaign > experiment > run. No research platform built. |

### Verified against real state

```json
{"commit": "b148e39f6c7fd817b494a9eeff0da02c3c4a096e",
 "short_commit": "b148e39f6c7f", "branch": "v1.0-shadow",
 "dirty": true, "code_version": "b148e39f6c7f+dirty",
 "is_fully_reproducible": false,
 "unreproducible_reasons": ["working_tree_dirty"]}
```

**Bujji's honest current identity is `b148e39f6c7f+dirty`.** Nothing produced right now is reproducible from the commit alone — the working tree has been uncommitted throughout this programme. The system now *says so* instead of implying otherwise.

---

## 4. Lineage graph after changes

```
RAW OBSERVATION            evidence_ids ✅   as_of ❌   versions ❌
   ↓
DOMAIN ASSESSMENT          assessment_id ✅  as_of ❌   versions ❌
   ↓
CONSENSUS / OPPORTUNITY    supporting_ids ✅ as_of ❌   versions ❌
   ↓
ELIGIBILITY → INTENT       supporting_ids ✅ as_of ❌   versions ❌
   ↓
   ┌──────────── NOW AVAILABLE (not yet attached at producers) ────────────┐
   │  DecisionContext.as_of      → injected event time, replay-stable      │
   │  RuntimeIdentity.code       → b148e39f6c7f+dirty                      │
   │  RuntimeIdentity.config     → content hash + per-config versions      │
   │  CalculationIdentity        → CV-<content hash of formula+params>     │
   │  ExperimentIdentity         → contract only                           │
   └───────────────────────────────────────────────────────────────────────┘
   ↓
POSITION / P&L / ATTRIBUTION / MEMORY      EventStore-backed ✅ replayable
```

**Change:** the version graph now *exists and resolves real values*. It is **not yet stamped onto producers** — that is deliberate. Attaching it to `intelligence_cycle_recorder` changes what every session writes, and doing that in the same phase that introduces the mechanism would conflate "does it work" with "did it change the corpus".

---

## 5. Replay reproducibility improvement

| Decision | Before 16D | After 16D | Blocking |
|---|---|---|---|
| A Opportunity | PARTIALLY | **PARTIALLY** (mechanism ready) | producer stamping |
| B Trade Intent | PARTIALLY | **PARTIALLY** (mechanism ready) | producer stamping |
| C Risk Approval | NOT | **NOT** | no risk layer in Stack B |
| D/E/F Position | REPRODUCIBLE | **REPRODUCIBLE** | — |

**The measurable gain is detection, not yet reproduction.** With `code_version` resolvable, a corpus produced by different code becomes *comparable*. Phase 15P's 684/708 divergence went unnoticed precisely because no version stamp existed to compare — that specific blindness is now closeable.

Honest limit: until producers stamp these values, existing corpora remain unstamped and A/B stay PARTIALLY_REPRODUCIBLE.

---

## 6. Timestamp discipline

The Phase 16C audit found exactly one wall-clock dependence on the decision path:

```python
# shadow_trade_construction/engine.py:181
as_of_date = source_cycle_id[:10] if source_cycle_id else datetime.now(timezone.utc)...
```

`DecisionContext.as_of_date` is the injected replacement — a replayed record is dated by the **record**, never by replay time.

**Not yet wired in.** Changing that call site alters `shadow_trade_construction` behaviour, which sits on the decision path; it deserves its own before/after replay comparison rather than being bundled here. The pinning test from 16C still holds it to exactly one occurrence.

---

## 7. Tests added (21, all passing)

Code identity resolves the real repo · dirty tree recorded not hidden · **non-repo yields `None`, never a placeholder** · `dirty=None` never treated as clean · config hash deterministic and order-independent · absent config never fabricates a hash · multi-config versions follow the `mil_next` pattern · `calc_version` content-derived and parameter-sensitive · unreproducibility *names its reason* · **adapts onto real `SessionManifest` without replacing it** · unresolved identity leaves manifest fields `None` · `DecisionContext` replay-stable · production artifacts carry no experiment identity · **reuses `fingerprint_state`, contains no `hashlib`** · impurity bounded to git inspection · **pure epistemics modules stay pure** · never touches broker/market data.

## 8. Regression

```
5,146  previous baseline
  +21  test_epistemics_identity.py
─────
5,167  ✅ 0 failed
```

No existing test or package modified. `git diff b148e39 -- bujji/` unchanged from the pre-16D state apart from the new file.

---

## 9. Not done, deliberately

| Deferred | Why |
|---|---|
| Stamping producers with identity | changes every session's output — needs its own before/after replay proof |
| Wiring `DecisionContext` into `shadow_trade_construction` | decision-path behaviour change |
| Research platform | contract only, as instructed |
| Replacing 50 free-text `provenance` strings | they are useful as `code_path`; replacing them is churn |

**Untouched as required:** TickStore · Market Data Fabric · watermark · storage architecture · ingestion topology · Stack A.

---

## 10. Next

**Phase 16E — Producer Stamping**: attach `RuntimeIdentity` + `CalculationIdentity` + `DecisionContext` at the recorder, with a before/after replay comparison proving the corpus changes only by *addition*, and a divergence detector that compares stamped versions instead of silently diverging.

That upgrades A/B from PARTIALLY_REPRODUCIBLE to REPRODUCIBLE — and is the last major reproducibility work that needs no Gate 1 evidence.

**Working tree uncommitted at `b148e39`. Gate 1 still blocked on token refresh + non-expiry session.**
