# Finding — Neutral Strategy Approval Gap on Range-Bound Days

**Status: documented, not fixed. To be revisited after the first live shadow session (2026-07-28), per the new workflow: `observed live -> reproducible -> root cause understood -> deterministic fix -> no strategy tuning`, or don't touch the code.**

## Where this came from

Raised during a pre-live-session walkthrough (2026-07-27 night) after a
simulated demo showed a directional strategy being selected on a
trending imaginary market. The question raised: real trading days are
mostly non-trending/choppy — is BUJJI actually equipped to deploy
neutral strategies (short straddle, short strangle, iron fly, iron
condor, butterfly) when conditions call for one, or does it lean on
directional trades by default?

## What the real 41-day corpus shows

Real thesis distribution (Series 111's own measured data, all 41 real
trading days, 2026-05-25 through 2026-07-22):

```
TREND_CONTINUATION      8
TREND_REVERSAL          3
RANGE_PERSISTENCE       13   <- single largest category
VOLATILITY_EXPANSION    7
VOLATILITY_COMPRESSION  3
BREAKOUT                6
NO_TRADE                1
```

**`RANGE_PERSISTENCE` + `VOLATILITY_COMPRESSION` = 16 of 41 days (39%)**
— confirming the premise: non-trending conditions are common in this
real corpus, not an edge case.

Per-day breakdown for those 16 days (real, re-run against the frozen
pipeline, 2026-07-27):

| Day | Thesis | Selected family | Selection confidence | Outcome |
|---|---|---|---|---|
| 2026-05-26 | RANGE_PERSISTENCE | *(none)* | — | NO_TRADE |
| 2026-06-01 | VOLATILITY_COMPRESSION | *(none)* | — | NO_TRADE |
| 2026-06-04 | RANGE_PERSISTENCE | *(none)* | — | NO_TRADE |
| 2026-06-05 | VOLATILITY_COMPRESSION | COVERED | MODERATE | NO_TRADE — `UNDEFINED_RISK_POLICY` |
| 2026-06-09 | RANGE_PERSISTENCE | NEUTRAL_PREMIUM_SELLING | HIGH | NO_TRADE — `UNDEFINED_RISK_POLICY` |
| 2026-06-17 | VOLATILITY_COMPRESSION | BUTTERFLY | HIGH | NO_TRADE — `GREEKS_UNAVAILABLE` |
| 2026-06-25 | RANGE_PERSISTENCE | *(none)* | — | NO_TRADE |
| 2026-07-01 | RANGE_PERSISTENCE | BUTTERFLY | HIGH | NO_TRADE — construction failed, `LIQUIDITY_INSUFFICIENT` |
| 2026-07-02 | RANGE_PERSISTENCE | *(none)* | — | NO_TRADE |
| 2026-07-03 | RANGE_PERSISTENCE | NEUTRAL_PREMIUM_SELLING | HIGH | NO_TRADE — `UNDEFINED_RISK_POLICY` |
| 2026-07-06 | RANGE_PERSISTENCE | BUTTERFLY | HIGH | NO_TRADE — `GREEKS_UNAVAILABLE` |
| 2026-07-09 | RANGE_PERSISTENCE | NEUTRAL_PREMIUM_SELLING | HIGH | NO_TRADE — `UNDEFINED_RISK_POLICY` |
| 2026-07-10 | RANGE_PERSISTENCE | BUTTERFLY | HIGH | NO_TRADE — `GREEKS_UNAVAILABLE` |
| 2026-07-16 | RANGE_PERSISTENCE | NEUTRAL_PREMIUM_SELLING | HIGH | NO_TRADE — `UNDEFINED_RISK_POLICY` |
| 2026-07-20 | RANGE_PERSISTENCE | BUTTERFLY | HIGH | **TRADE_APPROVED** |
| 2026-07-22 | RANGE_PERSISTENCE | *(none)* | — | NO_TRADE |

## The real finding

Strategy Selection is **not** the problem. On 10 of these 16 days it
correctly matched a neutral family — usually with HIGH selection
confidence, not a weak guess. The gap is entirely downstream, and it
splits into two structurally different causes:

### Cause 1 — `UNDEFINED_RISK_POLICY` (a real, deliberate policy, not a bug)

Every `NEUTRAL_PREMIUM_SELLING` selection (4/4) and one `COVERED`
selection were rejected by Portfolio Construction's own real policy:
undefined-risk families (naked short premium — theoretically unlimited
loss on an uncapped short straddle/strangle) are never approved,
regardless of confidence. This is Series 91's own deliberate,
disclosed risk control, not an oversight. It categorically blocks the
single most classic "sell premium when the market goes quiet" trade
family this project's own taxonomy defines.

**Open question for after live evidence accumulates**: should this
policy stay this strict during shadow-mode operation, where there is no
real capital to protect? Loosening it is a real strategy/risk-policy
decision, not an engineering fix — explicitly out of scope for an
"observed defect" change under the new rule, and worth a deliberate,
separate conversation rather than a quick toggle.

### Cause 2 — `GREEKS_UNAVAILABLE` on BUTTERFLY (looks like a real, narrow
engineering gap, not a policy)

`BUTTERFLY` is a DEFINED_RISK family (capped loss) and still failed 4 of
5 times for `GREEKS_UNAVAILABLE` at the Portfolio stage — only the
2026-07-20 attempt got through. Unlike Cause 1, this does not look like
an intentional risk control; it looks like a data-completeness gap
somewhere in portfolio-level Greeks aggregation for multi-leg
defined-risk structures. This is the more promising candidate for an
actual engineering fix, but has NOT yet been root-caused to a specific
line of code — only to the symptom (`GREEKS_UNAVAILABLE` as the
recorded rejection reason). One `LIQUIDITY_INSUFFICIENT` construction
failure (2026-07-01) is a separate, likely legitimate real-data gap
(the real chain that day may genuinely have lacked liquid strikes) and
is not part of this pattern.

## Real, honest summary number

**Of 16 real range-bound/compressed days, 10 correctly matched a
neutral strategy, and only 1 of those 10 (10%) survived to actual
approval.** 9 of 10 were blocked by one of the two causes above, not by
Strategy Selection failing to recognize the market condition.

## What NOT to do with this finding yet

Per the new workflow, this finding does not yet meet the bar for a code
change:
- ✔ Reproducible — yes, re-ran cleanly against the frozen pipeline.
- ✔ Root cause understood — partially: Cause 1 is fully understood (a
  policy, working as designed). Cause 2 is understood only at the
  symptom level (`GREEKS_UNAVAILABLE`), not yet traced to a specific
  root cause in the Greeks-aggregation code path.
- ✘ Observed in live market — not yet; this is corpus/replay evidence,
  not a live-session observation.
- Cause 1 is not a "defect" at all — changing it would be a strategy/
  risk-policy decision, explicitly excluded from "objective engineering
  defect" fixes.

**Action: revisit after 2026-07-28's live session.** If the live day
independently reproduces either pattern (a HIGH-confidence neutral
selection rejected for `UNDEFINED_RISK_POLICY` or `GREEKS_UNAVAILABLE`),
that's real-market confirmation, and Cause 2 specifically becomes worth
a proper root-cause investigation as a real engineering fix. Cause 1
stays a deliberate policy question for you to decide on, not something
to silently change.

## Addendum (2026-07-27, same night) — the hedged version already exists,
and is never chosen

Raised in follow-up discussion: a hedged short straddle/strangle does
not carry the unlimited-loss exposure `UNDEFINED_RISK_POLICY` exists to
block. That hedged shape is not missing from BUJJI — it already has a
name in the frozen taxonomy:

- Hedged short straddle = **`IRON_FLY`**
- Hedged short strangle = **`IRON_CONDOR`**

Both are already classified `DEFINED_RISK` (confirmed in
`msi_trade_construction.taxonomy.DEFINED_RISK_FAMILIES`, frozen, Series
90) — meaning neither is subject to the `UNDEFINED_RISK_POLICY` rejection
Cause 1 documents above. The capability this addendum's original request
asked for is not missing; it is dormant.

**Real, confirmed evidence (Series 111's own coverage measurement,
re-verified 2026-07-27):** across all 41 real trading days —
including the 16 range-bound/compressed days analysed above —
**`IRON_CONDOR` and `IRON_FLY` were selected zero times.** Not once, not
even on a day where the naked, undefined-risk `NEUTRAL_PREMIUM_SELLING`
was selected with HIGH confidence for what should be a structurally
similar market read.

### The real, sharper question this raises

Not "does BUJJI support hedged neutral strategies" (it does) or
"implement hedging" (nothing to implement) — the actual open question
is:

**Why does Strategy Selection consistently favour the naked,
undefined-risk family over its own hedged, defined-risk sibling, on the
same real market days?**

Candidate explanations, none yet confirmed — this is a real, open
question, not a diagnosis:
- A genuine, disclosed evidence gap (e.g. liquidity/premium data at the
  wing strikes `IRON_CONDOR`/`IRON_FLY` would need, that
  `NEUTRAL_PREMIUM_SELLING`'s narrower single-strike-pair shape doesn't
  require) causing suitability checks to legitimately favour the
  simpler family.
- A real ranking/scoring asymmetry in `msi_strategy_selection_foundation`
  or `msi_strategy_selector` that under-weights defined-risk multi-leg
  families relative to their undefined-risk single-pair counterparts,
  independent of the `UNDEFINED_RISK_POLICY` gate that comes later.
- Something specific to this 41-day corpus (real but narrow window) that
  would not generalise — only a live session, or a larger real corpus,
  can distinguish this from the two possibilities above.

### Status

**Not investigated further tonight, and not implemented — explicitly per
the stated rule** (observed live → reproducible → root cause understood
→ deterministic fix → no strategy tuning, otherwise don't touch the
code). This is a genuine, well-evidenced, but not-yet-root-caused
question. Revisit alongside Cause 2 (`GREEKS_UNAVAILABLE`) after the
2026-07-28 live session — if the live day reproduces the same pattern
(a naked neutral family selected over its own hedged sibling on a
range-bound read), that's real confirmation this is a genuine selection-
logic gap worth root-causing properly, not a corpus artifact.
