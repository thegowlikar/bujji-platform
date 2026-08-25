# Quant Research and Market Intelligence Architecture

Branch-only. Offline. Nothing here authorizes paper trading or live trading.

This document records what Bujji can honestly investigate, what each method
would require, and the framework built to reject weak ideas. It deliberately
ranks methods by **evidence readiness**, never by expected profitability.

---

## 1. Research sources, and what each actually supports

Honesty about sourcing comes first, because a citation nobody read is worse
than no citation: it lends borrowed authority to whatever follows it.

| Source | Retrieved? | What it supports here | What it does NOT support |
| --- | --- | --- | --- |
| Bailey & López de Prado, *The Deflated Sharpe Ratio* | Yes, summaries and method description | The multiple-testing correction in `evaluation.py`: the benchmark rises with trial count; minimum track record length; never report only the best backtest | Any claim about which strategies work |
| Andersen & Bollerslev (1998) and the realized-volatility literature | Yes, method description | 5-minute sampling as the practical standard in `diagnostics.py`; below it, microstructure noise dominates the estimator | A volatility forecast, or that RV is tradable |
| Variance risk premium literature (general) | Partially — well-documented negative premium in index options | Motivating VRP as a *candidate* to investigate | Any figure for Indian index options, or that VRP is currently harvestable |
| Chicago Fed (2025), *The Decline of the Variance Risk Premium* | **No — HTTP 403** | Nothing. Listed only so its absence is visible | Anything at all |
| Carr & Wu (2009), *Variance Risk Premiums* | **No — PDF not extracted** | Nothing | Anything at all |

**Consequence.** No method below is justified by a paper. Papers suggested what
to look at; only Bujji's own measured data decides what is testable.

---

## 2. Capability matrix — measured, not assumed

From the two preserved corpora of 2026-08-24, read through the offline
diagnostics. Reported per instrument class, never blended.

| Capability | lite sustained (5.64 h) | full ramp (1.08 h) |
| --- | --- | --- |
| records | 990,689 | 419,766 |
| distinct symbols | 249 (246 option, 2 index, 1 future) | 249 |
| option fields carried | **3**: `ltp`, `symbol`, `type` | 22 |
| two-sided quote observations | **0** | 412,503 |
| median option update rate | 0.068 msg/s | ~0.407 msg/s (understated: symbols joined progressively during the ramp) |
| options under 1 msg/60 s | 78 of 246 | — |
| median worst staleness | 251.5 s | — |
| p90 worst staleness | 6,286.9 s (1.75 h) | — |
| median relative spread | **not measurable** | 0.494% |
| p95 relative spread | **not measurable** | 9.524% |
| crossed books | n/a | 0 |
| median bid size | **not measurable** | 390 |
| zero-size quotes | n/a | 0 |
| sessions | 1 | 1 |

### The governing constraint

**Bujji's only long continuous capture is last-price-only.** The 5.64-hour
corpus carries no bid, no ask, and no size. It therefore cannot produce an
executable price, a spread, a depth estimate, or any P&L that claims to be
transactable. LTP does support mark-to-last valuation and simple price-based
thresholds — it does not support an exit price you could actually get.

The capture that does carry quotes covers **one hour of one day**. Every
quote-dependent method is currently limited by that, not by modelling skill.

---

## 3. Methods ranked by evidence readiness

Readiness = how close Bujji is to testing the idea **honestly**. This is not a
ranking by expected return, and a high rank is not encouragement.

| Rank | Method | Data required | Bujji has | Readiness |
| --- | --- | --- | --- | --- |
| 1 | **Descriptive OI positioning** | REST chain with `oi`/`prev_oi`, provenance, freshness | Yes — typed, verified, prev_oi now carried | **Testable descriptively today.** Never as a tick-synchronous fact |
| 2 | **Realized volatility / intraday variation profiling** | Prices at ≥5-min buckets across many sessions | 5-min buckets computable; 1 session only | **Method ready, sample absent** |
| 3 | **Liquidity and spread regime characterisation** | Two-sided quotes over full sessions | 1.08 h only | **Blocked on capture mode**, not on method |
| 4 | **Realized vs implied volatility (VRP)** | IV or a full chain with a settlement-quality mark, across many sessions | RV yes; IV not carried in the feed | **Blocked on an input Bujji does not collect** |
| 5 | **Intraday seasonal effects (open/close)** | Many sessions, exchange-time aligned | 1 session, receiver clock | **Blocked on sample size** |
| 6 | **Multi-day / overnight structure** | Many sessions | 1 | **Blocked** |
| 7 | **Any executable-P&L strategy test** | Bid/ask at decision AND exit instants, full session | 1.08 h of quotes | **Blocked** |
| 8 | **Intraday market making** | Full depth, queue position, sub-second updates | None; top-of-book only, 0.068 msg/s median | **Not testable. Not close** |
| 9 | **High-frequency signals** | Sub-second decisions | Median option updates every ~14.7 s | **Not testable. Not close** |

Ranks 8 and 9 are listed so they are visibly ruled out rather than quietly
never attempted.

---

## 4. Offline research architecture

`bujji/quant_research/` — seven modules, **absent from the reachability
closure of every declared entrypoint**, asserted by a test rather than claimed
in a docstring.

| Module | Authority it holds | Refusals |
| --- | --- | --- |
| `manifest.py` | Dataset identity: source hashes, universe identity, session date, phase classification | 7 |
| `dataset.py` | Event-ordered, phase-filtered, look-ahead-free access | 2 exception types |
| `features.py` | Feature values inseparable from source, timestamp and provenance | 2 |
| `diagnostics.py` | Descriptive measurement over a corpus | reports `INSUFFICIENT_DATA` rather than fabricating |
| `evaluation.py` | The acceptance contract | 11 gates |
| `ledger.py` | Append-only trial count | 1 |

**No new authorities were created.** These are offline readers over durable
artifacts the existing authorities already produce. Quote, OI, journal,
snapshot and decision-evidence authorities are untouched.

### The three structural rules

1. **A dataset that cannot prove what it is, is not usable.** The fingerprint
   covers the inclusion rule as well as the bytes — the same corpus filtered
   differently is a different dataset.
2. **Look-ahead is impossible, not merely discouraged.** `PointInTime.future()`
   raises. Forward outcomes come only from `label_forward_outcome()`, which
   stamps every result as a label.
3. **Tick facts and chain facts are different types.** They combine only
   through `join_with_staleness()`, which records the chain snapshot's age and
   refuses when that age is unknown.

---

## 5. Tests, negative controls, and A/B evidence

47 tests. **Eleven negative controls, each verified by defect injection** — the
guard was removed, the control was confirmed to fail, the guard was restored,
and the control was confirmed to pass. A control that cannot fire is not
evidence, and this project has shipped vacuous controls before.

| # | Planted defect | Fires |
| --- | --- | --- |
| 1 | `PointInTime.future()` returns data | Yes |
| 2 | INVALID phase refusal removed | Yes |
| 2b | UNMEASURED phase refusal removed | Yes |
| 3 | REST provenance collapsed into TICK | Yes |
| 4 | LTP-without-quotes P&L gate removed | Yes |
| 5 | Source hash no longer recomputed | Yes |
| 6 | Multiple-testing deflation skipped | Yes |
| 7 | Sequence-order assertion removed | Yes |
| 8 | Unknown chain age defaulted to zero | Yes |
| 9 | Future chain snapshot admitted | Yes |
| 10 | Ledger hides failed trials | Yes |

**A/B evidence.** Every control asserts the clean case passes as well as the
defective case failing, so the refusal is attributable to the planted defect
rather than to anything else in the fixture.

**A defect this file found in its own work.** The first deflated-Sharpe
implementation omitted the `sqrt(V)` scaling on the expected-maximum term,
leaving the threshold in standard-normal units while the observed Sharpe was in
per-observation units. Every result failed. That looks like rigour and is
actually a broken comparison — a gate that always fires proves nothing.
`test_deflation_is_not_vacuous` now guards it.

---

## 6. Minimum evidence before any supervised paper experiment

None of this is met today. All of it is required.

1. **≥ 20 distinct trading sessions** captured in a mode that carries bid, ask
   and size — not last price alone. One session measures that session.
2. **A full-session quote capture**, open to F&O close, with measured coverage
   per instrument class. The current quote evidence is 1.08 h.
3. **Field availability measured per class across those sessions**, so no
   method silently depends on a field that arrives 40% of the time.
4. **A stated, measured OI freshness distribution**, so the staleness bound in
   `join_with_staleness()` is chosen from evidence rather than assumed.
5. **≥ 100 independent observations** for any Sharpe-like statistic.
6. **A declared acceptance criterion, recorded before the result is seen.**
7. **An execution cost model** covering fees, spread, slippage and partial
   fills — measurable only once quotes exist across full sessions.
8. **A complete trial ledger**, failures included, so the deflation uses the
   true N.
9. **Out-of-sample evaluation after every policy choice is frozen.**

---

## 7. The next single best research experiment

**Not a strategy test. A capture-capability experiment.**

Everything that matters is currently blocked by one fact: the long capture has
three fields and the rich capture has one hour. No modelling work can move that.

**Experiment.** Run one full-session capture in full mode, 09:15 to the F&O
close, over the existing 249-symbol universe, and measure per instrument class:
two-sided quote coverage, update rate, spread distribution, worst staleness,
and field availability — with the universe/manifest evidence pair preserved.

**Succeeds if** two-sided quote coverage for options is high enough, and
staleness bounded enough, that an executable price exists at most instants a
decision would be made. That threshold must be declared before the run.

**Fails if** coverage or staleness make an executable price unavailable at
decision instants — in which case every quote-dependent method in the table
above stays blocked, and the honest next step is a capture-mode change, not a
strategy.

**Either outcome is a result.** A failure here is more valuable than a backtest,
because it prevents months of modelling on data that could never support it.

**This experiment observes. It places no order and takes no position.**

---

## 8. Statement

**No research output in this package authorizes paper trading or live trading.**

The strongest verdict the evaluation contract can return is `NOT_REJECTED`,
which means only that some evidence failed to refute a hypothesis. The
vocabulary contains no word for profitable. Nothing measured here — on one
session, on a last-price-only corpus, with no cost model and no out-of-sample
period — comes near the evidence required to risk capital, real or simulated.
