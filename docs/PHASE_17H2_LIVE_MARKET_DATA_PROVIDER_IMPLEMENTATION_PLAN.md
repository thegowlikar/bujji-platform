# Phase 17H.2 — LiveMarketDataProvider Implementation Plan

**Status: AUDIT + PLAN ONLY. No code. No schema changes. No new
dataclasses. No Layer 0 collectors. No websocket wiring.**

Final implementation audit against the four decisions locked in
`docs/PHASE_17H1_LIVE_PROVIDER_CONTRACT_DECISIONS.md`. Every claim below
is read directly from the real code on the VPS this session, not
recalled from an earlier summary.

---

## 1. Existing provider contract — read in full

`bujji/production_runtime/market_data_provider.py`:

```python
class MarketDataUnavailableError(Exception): ...

class MarketDataProvider(ABC):
    @abstractmethod
    def get_option_chain(self, as_of_date: str) -> Sequence: ...

    @abstractmethod
    def get_spot(self) -> Optional[float]: ...
```

**Method-by-method contract:**

| Method | Signature | Return | Exception expectation | Caller assumption (verified) |
|---|---|---|---|---|
| `get_option_chain` | `(self, as_of_date: str) -> Sequence` | A sequence of chain rows (no declared element type in the ABC itself — see §2 for `ReplayChainProvider`'s real element type) | `MarketDataUnavailableError` on failure, per the class's own docstring ("fail closed, never silently substitute") | Called twice per session (`_pre_market_check`, `_entry_window`), both times **synchronously**, both times wrapped by the runner in a broad `except Exception` that re-raises as `ConfigurationError` (pre-market check) or lets `MarketDataUnavailableError` propagate to `run()`'s own top-level catch (entry window) |
| `get_spot` | `(self) -> Optional[float]` | `None` if genuinely unavailable, per docstring: "never a guess or a stale carry-forward silently presented as current" | None declared — `Optional[float]` return is the failure signal, not an exception | Called **once**, in `_entry_window`, **always after** `get_option_chain()` has already succeeded in the same call — the runner never calls `get_spot()` standalone |

**Minimum implementation surface confirmed: exactly these two methods.**
No `__init__` signature is mandated by the ABC — `ReplayChainProvider`'s
own constructor (`bhavcopy_path`, `underlying`) is provider-specific, not
part of the contract.

---

## 2. `ReplayChainProvider` parity — what must match, what may legitimately differ

Full class re-read (`bujji/production_runtime/market_data_provider.py`,
lines 53–107):

```python
class ReplayChainProvider(MarketDataProvider):
    def __init__(self, bhavcopy_path: str, underlying: str = "NIFTY") -> None: ...
    def _ensure_loaded(self, as_of_date: str) -> None: ...
    def get_option_chain(self, as_of_date: str) -> Sequence: ...
    def get_spot(self) -> Optional[float]: ...
```

### Must remain identical at the contract level

1. **Caching within a session.** `_ensure_loaded`'s `if self._chain is
   not None: return` guard means the two `get_option_chain()` calls in
   one session hit the real source exactly once. `LiveMarketDataProvider`
   must do the same — the runner's pre-market probe and entry-window
   read must not double-poll FYERS. This is not optional parity; it is
   what makes the "exactly twice, but only one real fetch" pattern
   (Part 1.2 of the 17H audit) true for a live source too.
2. **`get_spot()` reads a value already populated by
   `get_option_chain()`**, never fetches independently. `ReplayChainProvider.
   get_spot()` is a bare `return self._spot` — no I/O. A live
   implementation should preserve this ordering dependency rather than
   have `get_spot()` make its own separate REST call, since the runner's
   real call order (`get_option_chain()` always first) already guarantees
   `_spot` is populated by the time `get_spot()` runs.
3. **Fail closed via `MarketDataUnavailableError`**, never a silent empty
   sequence or a guessed spot. `ReplayChainProvider._ensure_loaded`
   raises this exact exception on a read failure, an empty chain, or an
   invalid spot (`spot is None or spot <= 0`). A live implementation
   needs the same three failure checks, applied to a live response
   instead of a file read.
4. **`get_option_chain()`'s element shape.** `ReplayChainProvider` returns
   `tuple(s.observations()[-1] for s in series if len(s.observations()) > 0)`
   — real `OptionObservation` instances (from
   `bujji.options_observation.models`), not raw dicts. Per Decision 3
   (no new dataclasses), a live implementation must return the same
   element type — `OptionObservation` — reusing
   `bujji.options_observation.engine.build_option_observation()` (§4),
   not a bespoke shape.

### Legitimate differences because the source is live FYERS REST, not a file

1. **Per-strike construction vs. per-series-last-observation.**
   `ReplayChainProvider` reads pre-ingested `OptionObservationSeries`
   objects (built once, historically, by
   `options_observation.runner.ingest_all_option_series_from_bhavcopy`)
   and takes each series' last observation. A live provider has no
   series to begin with — it must call `build_option_observation()`
   directly, once per real chain row, with `origin=moc_taxonomy.
   ORIGIN_LIVE` (not `ORIGIN_REPLAY` or whatever the bhavcopy path uses)
   — a real, correct, and expected divergence, not a parity violation.
2. **Field availability profile is inverted, not just "fewer fields."**
   See §4 — this is the single most consequential legitimate difference
   and is detailed there rather than summarized here.
3. **Failure modes differ.** A file read fails with `OSError`
   (`ReplayChainProvider` catches this specifically). A live REST call
   fails with `AuthenticationError`, a timeout, a malformed response, or
   a structurally-empty response — none of which `ReplayChainProvider`
   has any code path for, because it has never needed one. §5 defines
   these.
4. **`as_of_date` becomes advisory, not a file-selection key.**
   `ReplayChainProvider` uses `as_of_date` to pass through to
   `ingest_all_option_series_from_bhavcopy` (which real bhavcopy file to
   parse). A live provider has no file to select — `as_of_date` for a
   live call is only meaningful as a sanity/logging value (does it match
   today's real date?), never as a parameter that changes which broker
   endpoint gets called. This asymmetry is inherent to what "live" means
   and is not a contract violation.

---

## 3. FYERS transport boundary audit

### `get_spot()` — an existing method already does most of this, with one real signature mismatch

`FyersBroker.get_spot(underlying: str) -> float` (line 267) exists,
already live-verified (via the `_quote()`/`ltp` path already certified
2026-08-12 and reconfirmed live in Gate B). **But its signature does not
match what `MarketDataProvider.get_spot()` needs directly:**

- `FyersBroker.get_spot()` returns a bare `float` and **raises
  `KeyError`** if the symbol isn't found in the response (`_quote()`'s
  own code: `raise KeyError(f"symbol {symbol} not found...")`).
- `MarketDataProvider.get_spot()` must return `Optional[float]` — `None`
  on failure, never an exception, per its own docstring.

**This is a real translation `LiveMarketDataProvider` must perform, not
a gap in `FyersBroker`**: catch `KeyError` (symbol not found) and
`AuthenticationError` (raised by `_raise_if_auth_error` inside `_quote()`
before the `KeyError` path is even reached) and translate both into
either `None` (if `MarketDataProvider.get_spot()`'s contract is followed
literally) or into letting the exception propagate up through
`get_option_chain()`'s `MarketDataUnavailableError` instead (since,
per §2 item 2, `get_spot()` is never called independently — a spot
fetch failure inside `get_option_chain()`'s own internal sequencing can
fail the WHOLE chain fetch closed, consistent with `ReplayChainProvider`'s
own "spot invalid → raise `MarketDataUnavailableError`" behavior). **This
ordering choice is a real implementation decision, not resolved by this
document** — see §7 item 1.

**No transport change needed.** `FyersBroker.get_spot()` is sufcient
as-is; only the calling/translation layer in `LiveMarketDataProvider`
needs to reconcile the two signatures.

### `get_option_chain()` — the existing method is insufficient; the raw method is the right one

Confirmed (17F.7, reconfirmed this session): `FyersBroker.
get_option_chain(underlying, spot, strike_count) -> Optional[list[tuple[
float, float, float]]]` extracts **only** `strike_price`/`option_type`/
`oi` — bare `(strike, ce_oi, pe_oi)` tuples, no premium, no identity
beyond strike. **Not usable** to build a real `OptionObservation` (needs
`ltp`/`bid`/`ask`/OI at minimum, per §4).

`FyersBroker.get_option_chain_raw(underlying, strike_count) -> Optional[
dict]` (added Phase 17F.7.1) **is** the right transport method — a raw,
unmodified pass-through of the full `optionchain` response, already
proven live in Gate B (`fyers_option_chain_discovery_20260813.json`).
`LiveMarketDataProvider` should call this, not `get_option_chain()`.
**No transport change needed here either** — the raw method already
exists and is already live-verified.

### Missing transformations/error handling — real, specific gaps

1. **No existing FYERS method returns per-strike `OptionObservation`-ready
   data.** `get_option_chain_raw()` returns the untouched dict; the
   row-by-row extraction (strike, option_type, ltp, bid, ask, oi, oich,
   prev_oi, symbol → `build_option_observation()`'s parameters) does not
   exist anywhere yet. This is `LiveMarketDataProvider`'s own
   responsibility to write — not a transport gap, a provider-layer gap.
2. **No existing method resolves an option's real `expiry` as a
   normalized string** for a live chain row. Gate B's raw capture shows
   `symbol: "NSE:NIFTY2681824100CE"` (expiry embedded in the symbol
   string, e.g. `2681` = a FYERS date-code, not ISO8601) but the
   `optionchain` response's per-strike rows carry **no separate `expiry`
   field** (confirmed: `expiry` appears only on the `depth` endpoint's
   rows, not on `optionchain`'s — a genuine cross-endpoint inconsistency,
   not previously stated this precisely). `build_option_observation()`
   requires `expiry: str` as a parameter. **Resolving expiry from the
   symbol string (or from a separate call) is a required transformation
   with no existing implementation** — flagged as a real missing piece,
   not assumed solvable trivially.
3. **`AuthenticationError` handling is already consistent** across every
   method touched here (`get_spot`→`_quote`, `get_option_chain_raw`) —
   both call `self._raise_if_auth_error(data)` before any other
   processing, matching the pattern this whole engagement has used
   throughout. No new error-handling pattern needs inventing for auth
   specifically.

**No transport code is modified by this document**, per the explicit
constraint — every gap above is named as a requirement for
`LiveMarketDataProvider`'s own code, in a later phase.

---

## 4. Data contract mapping — real Gate B fields into `OptionObservation`, with an inverted-availability finding

### Spot quote → `MarketDataProvider.get_spot()`

Straightforward: `FyersBroker.get_spot("NIFTY")` (via `_quote()`/`ltp`)
returns the real `lp` field, live-verified twice now (2026-08-12
certification, 2026-08-13 Gate B websocket cross-check). No mapping
ambiguity.

### Option chain rows → `OptionObservation` — the inverted-availability finding

`build_option_observation()`'s parameters map onto Gate B's real
`optionchain` fields as follows:

| `build_option_observation()` param | Live REST field (Gate B, verified) | Available? |
|---|---|---|
| `strike` | `strike_price` | Yes |
| `option_type` | `option_type` | Yes |
| `instrument_symbol` | `symbol` | Yes |
| `open_`/`high`/`low`/`close`/`settlement` | **none** | **No — confirmed absent from every one of 22 real rows** |
| `volume` | `volume` | Yes |
| `open_interest` | `oi` | Yes |
| `change_in_open_interest` | `oich` (FYERS's own pre-computed delta) | Yes |
| `underlying_price` | Not on the per-strike row; present as `ltp`/`fp` on the SEPARATE underlying row (`strike_price: -1`) | Yes, but requires reading a different row than the strike row |
| `bid` | `bid` | **Yes** |
| `ask` | `ask` | **Yes** |
| `bid_quantity`/`ask_quantity` | **none** — Gate B's `optionchain` rows carry scalar `bid`/`ask` prices only, no size | **No** |
| `expiry` | Not on this endpoint's rows at all (§3 finding) | **No — separate resolution needed** |
| `timestamp` (MOC identity) | **No timestamp field anywhere on this endpoint** (confirmed, `FYERS_REALITY_PAYLOAD_CONTRACT.md` §1) | **No** |

**The finding this document exists to surface:** `taxonomy.
MANDATORY_OPTIONS_OBSERVATION_FIELDS` (`OPEN, HIGH, LOW, CLOSE,
SETTLEMENT, VOLUME, OPEN_INTEREST, CHANGE_IN_OPEN_INTEREST`) was designed
around Bhavcopy's availability profile — where OHLC+settlement are
**always present** and BID/ASK are **structurally absent**
(`KNOWN_UNAVAILABLE_FROM_BHAVCOPY`). **Live REST is the exact inverse**:
BID/ASK are present, OHLC+settlement are absent. Reusing
`MANDATORY_OPTIONS_OBSERVATION_FIELDS` unmodified for a live-sourced
`OptionObservation` would mark **every single live row permanently
INCOMPLETE** for 5 of its 8 mandatory fields — not because of a real
data gap on that row, but because this source structurally can never
supply them, exactly the situation `KNOWN_UNAVAILABLE_FROM_BHAVCOPY`
exists to distinguish from a genuine gap, applied to the wrong source.

**This is a real design decision, explicitly NOT made by this
document** (no schema changes authorized here): does a live-REST source
get its own `KNOWN_UNAVAILABLE_FROM_LIVE_REST_CHAIN = (FIELD_OPEN,
FIELD_HIGH, FIELD_LOW, FIELD_CLOSE, FIELD_SETTLEMENT)` and a
correspondingly source-aware mandatory set, mirroring the Bhavcopy
pattern exactly? Or does `completeness` simply read low for every live
row, honestly reflecting that this source knows less about OHLC than
Bhavcopy does? Both are defensible; neither is decided here. Flagged as
a required decision before `LiveMarketDataProvider` is actually coded
(§7 item 2), because getting this wrong either fabricates false
"INCOMPLETE" alarms on every live row forever, or silently reuses a
Bhavcopy-shaped assumption for a structurally different source.

### Timestamps / source lineage

- **`event_timestamp`**: `optionchain` provides none (confirmed). Per
  Layer0Lineage's own "never backfilled" rule (already established, no
  new decision needed): must be `None`, explicitly, never substituted
  with capture time.
- **`capture_timestamp`**: the real poll moment — always available,
  supplied by the provider, never the broker.
- **Source/lineage**: `origin=moc_taxonomy.ORIGIN_LIVE` (the constant
  already exists and is already used elsewhere in `market_reality/
  capture.py` for exactly this purpose), `source="fyers"`,
  `access_method="direct_sdk_fyers_broker_py"` — all pre-existing
  values, no new vocabulary needed.

### Explicit unavailable-information summary (no synthetic values, restated)

- No OHLC/settlement on any live option chain row — must be `None`, not
  interpolated, not carried forward from a stale bhavcopy row.
- No bid/ask quantity — `None`, not assumed from `volume`.
- No expiry field on the chain endpoint — must be genuinely resolved
  (from the symbol string or a separate call), never hand-typed/guessed
  per contract.
- No event timestamp on the chain endpoint — `None`, permanently, for
  this source.

---

## 5. Failure semantics — mapped onto the existing, closed capture vocabulary only

Per Decision 4 (17H.1), **no new `taxonomy.REASON_*` constant is
authorized**. Each failure mode below is mapped onto what already
exists, or explicitly left as a single-call failure with no capture
event (consistent with 17H.1's own "a single failed poll is not, by
itself, evidence of a market-reality gap" reasoning):

| Failure mode | `MarketDataProvider` surface | Capture event? |
|---|---|---|
| **Authentication failure** | `AuthenticationError` from `FyersBroker` → caught, re-raised as `MarketDataUnavailableError` (fail-closed, matching `ReplayChainProvider`'s own discipline of never leaking a raw broker exception through the ABC boundary) | `CaptureLifecycleTracker.record_condition(reason=REASON_AUTH_FAILURE)` — a genuine ongoing condition (every subsequent call fails identically until token refresh), correctly using the open/close pairing |
| **Rate limit** | Same — caught, re-raised as `MarketDataUnavailableError` | `record_point_event(reason=REASON_RATE_LIMIT_SKIP)` — single skipped poll, per 17H.1 |
| **Timeout** | Same — caught, re-raised as `MarketDataUnavailableError` | **No capture event for a single timeout**, per 17H.1's explicit deferral — sustained-timeout policy (threshold, `REASON_COLLECTOR_RESTART` if the provider itself restarts) is not decided by this document |
| **Malformed response** (e.g. `get_option_chain_raw()` returns `None` because `"data"` key is absent) | Treated identically to `ReplayChainProvider`'s "zero usable rows" case — raise `MarketDataUnavailableError` with a message naming what was actually missing (mirroring `ReplayChainProvider`'s own real error-message discipline: `f"bhavcopy {path!r} produced zero usable option-chain rows..."`) | None — a single malformed response is a call-level failure, not a capture-worthy condition per 17H.1 |
| **Empty market response** (real, structural — e.g. zero strikes returned, matching `test_get_option_chain_returns_empty_list_for_no_real_strikes`'s already-proven case at the `FyersBroker` level) | `MarketDataUnavailableError` — **this is NOT a broker limitation to paper over**; an empty chain during market hours is itself a finding, exactly like `ReplayChainProvider`'s existing `"produced zero usable option-chain rows"` check | None by default; if this becomes a *repeated* pattern across many calls, that would be the same sustained-failure question left open in §5's timeout row, not decided here |

**No new exception type is introduced.** `MarketDataUnavailableError`
(already defined in `market_data_provider.py`) is reused for every live
failure path — consistent with Decision 3's "no new dataclasses" and the
general reuse discipline, extended here to exceptions as well.

---

## 6. Testing strategy — required before implementation

Modeled directly on `tests/test_fyers_transport_mapping.py`'s existing
`RecordingFyers` pattern (already used for `get_depth()`,
`get_option_chain_raw()`, and every other `FyersBroker` method this
engagement has added) and `tests/test_options_os_runner.py`'s existing
provider-parity tests (`test_replay_chain_provider_loads_real_bhavcopy`,
`test_replay_chain_provider_missing_file_fails_closed`) — both patterns
already exist in this codebase and should be extended, not reinvented.

### Required test categories, none of which touch a real network

1. **Fake FYERS transport.** A `RecordingFyers`-style subclass (or reuse
   of the existing one) that returns canned `optionchain`/`ltp` responses
   shaped **exactly like Gate B's real captures** — not invented shapes.
   The canned fixtures should be lifted directly from
   `fyers_option_chain_discovery_20260813.json`'s real rows, the same
   discipline `test_get_option_chain_parses_the_nested_data_key`
   already uses ("Shaped exactly like the real live capture").
2. **Deterministic responses, no network dependency.** Every test
   constructs `LiveMarketDataProvider` with a fake broker; zero tests in
   this category may require `market hours`, a real token, or SSH access
   — mirroring every existing `test_fyers_transport_mapping.py` test.
3. **Contract-compatibility tests, direct parity with `ReplayChainProvider`'s
   own test suite:**
   - `test_live_provider_caches_chain_within_a_session` — two
     `get_option_chain()` calls hit the fake transport exactly once
     (mirrors `_ensure_loaded`'s guard, §2 item 1).
   - `test_live_provider_get_spot_reads_the_already_loaded_value` — no
     second network call inside `get_spot()` (§2 item 2).
   - `test_live_provider_fails_closed_on_auth_error` (§5).
   - `test_live_provider_fails_closed_on_empty_chain` — mirrors
     `test_replay_chain_provider_missing_file_fails_closed`'s shape,
     substituting a live-empty-response fixture for a missing file.
   - `test_live_provider_never_populates_ohlc_fields` — asserts every
     constructed `OptionObservation` has `open`/`high`/`low`/`close`/
     `settlement` all `None`, never fabricated (§4's central finding,
     turned into an enforced test rather than only documented).
   - `test_live_provider_never_populates_expiry_with_a_guess` — once §3
     item 2's resolution approach is decided, a test proving it never
     silently defaults.
   - `test_live_provider_returns_optionobservation_instances` — asserts
     the return type of `get_option_chain()` is the same
     `bujji.options_observation.models.OptionObservation` type
     `ReplayChainProvider` returns, not a dict or a new type (Decision 3,
     enforced).
4. **Capture-lifecycle interaction tests** (once §5's mapping is
   implemented): `record_condition`/`record_point_event` called with the
   correct, existing `taxonomy.REASON_*` value for each real failure
   fixture — reusing `CaptureLifecycleTracker`'s own already-passing test
   patterns (17F.5) as the model, not reinventing tracker-level
   assertions inside the provider's own test file.
5. **Safety-scan extension**, matching this project's standing
   convention (`test_market_reality_safety.py`, `test_market_timeseries_safety.py`):
   if `LiveMarketDataProvider` lives under `bujji/production_runtime/`,
   confirm whether that package already has an equivalent AST safety
   scan; if not, this is a decision for the implementation phase (not
   authorized here) on whether one should be added.

**No implementation is authorized by this section — it defines what
must exist before/alongside `LiveMarketDataProvider`'s first line of
real code, matching the phase's own "code-ready plan, not
implementation" framing.**

---

## 7. Decisions still required before coding (explicitly not resolved by this document)

1. **`get_spot()` failure propagation** (§3): does a spot-fetch failure
   inside `get_option_chain()`'s internal flow raise
   `MarketDataUnavailableError` immediately (failing the whole chain
   fetch), or does `LiveMarketDataProvider.get_spot()` independently
   return `None` per the ABC's literal contract? `ReplayChainProvider`'s
   own precedent (spot validated inside `_ensure_loaded`, not inside
   `get_spot()` itself) suggests the former, but this is not stated as
   decided here.
2. **Source-aware completeness/mandatory-fields for live REST** (§4) —
   the single most consequential open decision in this document. Without
   it, every live-sourced `OptionObservation` will read as permanently
   `INCOMPLETE`, which is either the honest truth or a misleading
   Bhavcopy-shaped assumption depending on how `completeness` is
   consumed downstream — not evaluated here since evaluating that
   consumption is itself out of scope for this phase.
3. **Expiry resolution method** (§3 item 2) — parse from the FYERS symbol
   string's date-code, or a separate resolved call (mirroring
   `verify_fo_access.py`'s existing `InstrumentMaster.resolve_atm()`
   pattern, per the 17F.7 audit's own note that this is the one
   live-verified symbol-resolution mechanism this codebase already has).
4. **Sustained-failure threshold for REST timeouts/malformed responses**
   (§5, carried forward unchanged from 17H.1 Decision 4) — deferred to
   whichever phase actually builds a continuously-running collector,
   since `LiveMarketDataProvider` itself (§2 of 17H.1) is called only
   twice per session and may never need this threshold at all if it
   never runs long enough to accumulate "sustained" failures.

None of these four are authorized to be resolved by writing code under
this document. Phase 17H.2 implementation itself remains not started.
