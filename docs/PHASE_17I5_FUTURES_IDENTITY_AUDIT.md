# Phase 17I.5 — Futures Identity Audit

**Status: AUDIT ONLY. `InstrumentMaster` was not modified.**

Every finding below is read directly off the real, currently-cached NFO
CSV on the VPS (`/opt/bujji/app/data/instrument_master/fyers_fo_NSE.csv`,
14,386,115 bytes, last downloaded 2026-08-12, 78,218 total rows) — not
inferred from documentation or the existing option-resolution code path.

---

## Question 1 — Does the NFO CSV actually contain futures rows?

**Yes, confirmed directly.** Filtering the real cached file for
`underlying == "NIFTY"` and `option_type == "XX"` returns exactly 3 rows:

```
['101126082558072', 'NIFTY 25 Aug 26 FUT', '11', '65', '0.1', '', '0915-1540|1815-1915:',
 '2026-08-11', '1787652600', 'NSE:NIFTY26AUGFUT', '10', '11', '58072', 'NIFTY', '26000',
 '-1.0', 'XX', '101000000026000', 'None', '0', '0.0']

['101126092968407', 'NIFTY 29 Sep 26 FUT', ..., '1790676600', 'NSE:NIFTY26SEPFUT', ...]

['101126102748704', 'NIFTY 27 Oct 26 FUT', ..., '1793095800', 'NSE:NIFTY26OCTFUT', ...]
```

One row per upcoming calendar month (Aug/Sep/Oct 2026) — consistent with
NIFTY futures being monthly-only (no weekly futures contracts exist on
this underlying), matching the 3-month near/next/far structure NSE
actually lists.

## Question 2 — Are futures rows discarded intentionally?

**Yes, deliberately, not an oversight.** Two independent pieces of
evidence:
- `instrument_master.py`'s own module docstring already documents this
  from an earlier (2026-07-19) audit: *"option type (`CE`/`PE`, or `XX`
  for futures -- futures rows are skipped)."*
- `_rows_for()`'s filter (`if opt_type not in ("CE", "PE"): continue`)
  is covered by a dedicated existing test,
  `test_excludes_futures_rows` (`tests/test_instrument_master.py:97`):
  *"A futures row (option_type XX) for the same underlying must never be
  mistaken for an option contract."*

This means the fix must **add** a new futures-reading path, not **remove**
the existing filter — `_rows_for()` and `resolve_atm()` must keep
excluding futures rows exactly as they do today, or the existing test
(and the real guarantee it protects — an option resolver never
accidentally resolving a futures contract) breaks.

## Question 3 — What fields exist for futures rows?

**The exact same column layout as options**, verified on these real
rows, not assumed from the shared CSV format:

| Column | Index | Futures row value (near-month example) | Matches options' column? |
|---|---|---|---|
| Lot size | 3 | `65` | Yes — identical to NIFTY options' live-verified lot size |
| Expiry epoch | 8 | `1787652600` | Yes — same column, real epoch, consistent with the row's own human-readable description ("25 Aug 26") |
| FYERS symbol | 9 | `NSE:NIFTY26AUGFUT` | Yes — and matches `_futures_symbol()`'s existing construction convention exactly |
| Underlying | 13 | `NIFTY` | Yes |
| Strike | 15 | `-1.0` | Same sentinel convention already seen on non-option rows (matches the option chain discovery's own underlying-row `strike_price=-1` finding, Gate B) |
| Option type | 16 | `XX` | The one column that structurally distinguishes a futures row from CE/PE |

No new or futures-specific column layout exists — futures rows are
structurally identical NFO rows, just with `option_type="XX"` and a
sentinel strike.

## Question 4 — Can expiry be resolved authoritatively?

**Yes, directly, with the same reliability already live-verified for
options.** `expiry_epoch` (index 8) is present and populated on every
real futures row found — no parsing, no guessing, no reliance on
`_futures_symbol()`'s wall-clock-driven, self-documented-as-"provisional"
symbol construction. Notably, the *symbol* this audit read directly from
the real CSV (`NSE:NIFTY26AUGFUT`) matches what `_futures_symbol()`
already constructs today — reassuring, but this audit's conclusion is to
treat the CSV as the authoritative source going forward and
`_futures_symbol()` as a fallback only, not the reverse.

---

## Decision: (A) small resolver extension is enough

**Not** a larger `InstrumentMaster` redesign. Concretely, what's needed:

1. A new, separate row-reading path for `option_type == "XX"` rows —
   additive, not a modification of `_rows_for()`'s existing CE/PE filter
   (preserves `test_excludes_futures_rows` unchanged, per the "no
   existing tests should regress" constraint).
2. A new small resolver (e.g. "nearest upcoming futures expiry for this
   underlying") reusing the exact same nearest-expiry selection logic
   `resolve_atm()` already uses (`min` over `expiry_epoch >= today`) — no
   new selection algorithm.
3. **No new dataclass.** `OptionContract` cannot be reused for a futures
   identity — it requires `option_type: OptionType`, an enum with only
   `CE`/`PE` values, structurally inapplicable to a futures contract. The
   new resolver should return the primitives Layer 0 actually needs
   (symbol, an ISO expiry date string, lot size) rather than force-fitting
   an options-shaped object, and rather than inventing a new dataclass
   for a three-field return.

This is genuinely the smaller of the two options named in the original
question — the data is already there, in the expected shape, at the
expected reliability; the only real work is reading rows the code
currently throws away, additively.

## What this audit does NOT authorize

Per the phase constraint, `InstrumentMaster` was not modified this turn.
This finding only answers "is the small-extension path viable" (yes) —
implementing it is scoped as a task inside
[docs/PHASE_17I6_MINIMUM_CAPTURE_IMPLEMENTATION_PLAN.md](PHASE_17I6_MINIMUM_CAPTURE_IMPLEMENTATION_PLAN.md).
