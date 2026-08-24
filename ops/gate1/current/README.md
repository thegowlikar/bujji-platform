# Gate 1 — current (changes made after the v1 baseline)

`deployed-v1/` is the IMMUTABLE record of what actually ran on 2026-08-24.
This directory holds only the files changed since, so each change reads as a
diff against that baseline rather than replacing it.

Anything absent here is unchanged from `deployed-v1/` and still authoritative
there.

| file | change |
|---|---|
| `scripts/gate1_build_universe.py` | manifest schema `/1` → `/2`; adds `symbols_sha256` and `symbols_sha256_method` |
| `scripts/gate1_preflight.py` | unconditional paired validation; explicit v1 / v2 / unknown schema handling |
| `tests/test_manifest_pair.py` | new — 16 checks over the pair contract |
| `tests/test_preflight_ordering.py` | fixture repaired; it was relying on the gap v2 closes |

## What the two hashes answer

`universe_sha256` hashes `universe.json` **as bytes**. It changes if a key is
reordered, indentation shifts, or an unrelated field is added — and it cannot
say *which symbols* were subscribed unless you still hold that exact file.

`symbols_sha256` hashes the symbol **set**: sorted, newline-joined, UTF-8. It
is stable across formatting and instrument ordering, and changes if and only
if membership changes.

Measured on real artifacts: this morning's universe and a rebuild from the
same spot produced **different** `universe_sha256` and the **same**
`symbols_sha256` — two different files, one identical symbol set. A v1
manifest cannot make that distinction at all.

**The symbol hash verifies a candidate set you already hold. It cannot
reconstruct a lost `universe.json`.** The pair still requires both files,
which is why every validation branch refuses rather than degrading to a
partial check.

## Deployment reality

These files are still copied by hand to `/opt/bujji/gate1-run`. That
divergence is real, and removing it is what the production-structure work is
for. Until then this directory is the versioned *record*, not the deployment
*mechanism*.
