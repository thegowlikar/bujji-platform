# BUJJI Options OS — Design & Implementation Decisions

Scope note: this file covers decisions made **during this engagement** (the Trading Brain v3 execution/valuation/exit work, reliability sprints, and LSQ-1), which I can justify directly with evidence. Where a decision predates this engagement and is documented elsewhere in the repo (e.g. the legacy ORB-VWAP system's strategy rules, or the capital margin certification), I cite the source doc rather than fabricate a rationale I don't have direct knowledge of.

---

## Decisions made during this engagement

### D1. Fixed the `limit_price`/`reference_price` conflation by adding a dedicated field, rather than reusing `limit_price`
**Chosen**: add `reference_price: Optional[float]` to `bujji.core.models.OrderRequest`, structurally separate from `limit_price`.
**Rejected alternative**: keep threading the observed market price through `limit_price` (the original interim fix from the "Live Shadow Real-Time Paper Execution" sprint).
**Why**: direct execution proved `FyersBroker.place_order()` uses `type = 2 if request.limit_price is None else 1` — the mere *presence* of `limit_price` silently converts a market order into a real limit order. This was found via a user-requested micro-review, then confirmed and closed with executed proof (varying `reference_price` from 1.0 to 9999.0 while holding `limit_price` constant produces a byte-identical real SDK call). See `docs/SEMANTIC_CLEANUP_SPRINT.md`.

### D2. `PaperBroker` fill-price priority: `reference_price` → `limit_price` → synthetic default
**Chosen**: check `reference_price` first, fall back to `limit_price` (preserving every existing caller that only ever set that field), fall back to the original hardcoded `120.0` default.
**Why**: backward compatibility was a hard requirement ("No behavior change otherwise" per the sprint spec) — every existing test constructing an `OrderRequest` with only `limit_price` had to keep passing unmodified. Verified: 0 pre-existing tests needed modification.

### D3. Exit Engine kept strictly as a "consumer" — no I/O, no MTM math, no broker code
**Chosen**: `bujji/trading_brain/exit_engine/engine.py::evaluate()` is a pure function taking an already-computed `PortfolioValuation` + positions + config; closing-order construction is a **separate** function (`order_builder.py`), not part of the decision engine itself.
**Rejected alternative**: a single `ExitEngine` class doing evaluation AND order construction together.
**Why**: explicit sprint instruction ("Nothing else"); enforced structurally by an AST-walking test proving no broker/fyers import exists in `models.py`. This also makes the engine trivially replay-safe (same inputs + clock → same decision, proven by a determinism test) without needing to reason about I/O ordering.

### D4. Strategy Exit rule implemented as a documented placeholder that always returns `False`
**Chosen**: a real, named function (`_rule_strategy_exit`) that exists in the rule-priority vocabulary but structurally never triggers, with an explicit docstring explaining why.
**Rejected alternative**: inventing plausible-looking strategy-aware exit logic to make the feature look complete.
**Why**: the EQ1 qualification sprint's own real audit found zero strategy-specific exit logic exists anywhere in Trading Brain v3 today (its Gap G5). The sprint's own instruction was explicit: "Do NOT invent advanced logic." Fabricating logic here would have been a real, disclosed dishonesty risk this whole engagement has consistently avoided.

### D5. Fixed exit-rule priority: Maximum Loss > Profit Target > Hard Time Exit > Strategy Exit
**Chosen**: capital protection evaluated first, unconditionally, before any profit-taking or scheduled-exit logic.
**Why**: if a max-loss and a profit-target threshold were ever misconfigured to both be simultaneously satisfiable (a config error, not a normal state), the system should report the loss-protection reason, never the profit one. Verified with a dedicated test proving Max Loss wins over Hard Time Exit even when both are satisfiable at the same tick.

### D6. Missing an observed price is never treated as a loss or a profit
**Chosen**: both `Maximum Loss` and `Profit Target` rules structurally refuse to evaluate when `PortfolioValuation.total_pnl is None` — the resulting `NO_EXIT` decision is reported at `LOW` confidence rather than `HIGH`, honestly reflecting that the system doesn't actually know where it stands.
**Why**: matches this whole codebase's own established discipline (seen throughout the MSI packages and Trading Brain v3) of never fabricating a reading when real data is missing — "UNKNOWN" is always a legitimate, honest state, never silently coerced to a default.

### D7. Chose to build a NEW pure-function decision pipeline (Trading Brain v3) rather than extend the legacy Orchestrator/FSM system
**Chosen**: `bujji/trading_brain/*` + `bujji/production_runtime/*`, entirely separate from `bujji/core/orchestrator.py`.
**Why**: per `TRADING_BRAIN_CONSTITUTION.md`'s own explicit directive — "You are no longer extending... Those projects are complete. They become infrastructure." This was a pre-existing architectural decision I inherited and worked within, not one I made — cited here because it explains why the repo has 3 generations rather than 1 continuously-evolved system.

### D8. Deterministic content-hash IDs (`hashlib.md5`) everywhere, never `uuid4()`
**Chosen**: every ID in Trading Brain v3, MSI packages, and this engagement's own new code (Portfolio Valuation, Exit Engine, journals) is an md5 hash of real content + timestamp.
**Why**: makes replay determinism directly testable — running the same inputs through the same clock twice produces byte-identical IDs, proven repeatedly (e.g. the Exit Engine's own replay-consistency tests, `run_shadow()`'s own documented determinism guarantee). `uuid4()` would make this unverifiable. This was an established codebase convention I followed, not one I introduced.

### D9. Used real historical Bhavcopy data as the evidentiary substitute for live-market verification, every time a live session wasn't feasible
**Chosen**: rather than claim a live FYERS session ran when it didn't (no market hours / no fresh token available in a given turn), used the exact same real code paths driven by real, previously-captured market data (e.g. 3 real consecutive trading days' Bhavcopy premiums for the Exit Engine's own lifecycle demonstration).
**Why**: this whole engagement's own standing discipline — "evidence over optimism," never fabricate a claim of live verification. Every report explicitly discloses when this substitution was used and why.

### D10. LSQ-1 tooling built as small, real, independently-testable scripts (`tools/lsq_*.py`) rather than one monolithic runner
**Chosen**: separate consistency-checker, daily-report generator, and Chief Engineer's Log generator, each independently invokable and smoke-tested against real data before being trusted.
**Why**: this smoke-testing directly caught a real bug in the consistency checker's own first draft (`ledger_flat` compared against the wrong valuation record — fixed with a timestamp-based comparison). A monolithic script would have made this class of bug harder to isolate and fix. See `docs/LSQ1_PROTOCOL.md`.

### D11. LSQ-1 Chief Engineer's Log confidence score always shows its own arithmetic
**Chosen**: `Overall confidence: X.X/10` is always accompanied by a list of named deductions with real, disclosed amounts — never presented as a bare number.
**Why**: explicit user request ("a number picked for optics" was the thing to avoid) and consistent with this engagement's own repeated practice (e.g. the Production Readiness Gate Review's own confidence-scoring justification).

---

## Decisions that predate this engagement (documented in the repo, cited not fabricated)

### D12. Capital policy set to `CERTIFIED` with `margin_provider_certified: true`
**Source**: `docs/AUDIT_LOG.md`, "Pass 8 — span_margin LIVE CERTIFICATION COMPLETE."
**What was chosen and why** (as documented): a real, live FYERS SPAN margin API call was made against a real account, discovering and fixing a real bug (the response's margin figures are nested under a `data` key, not top-level — the pre-certification code was reading a non-existent top-level field and would have silently returned `None` on every real call). Also confirmed real multi-leg hedging-benefit behavior on a live NIFTY straddle (combined margin ≈ single-leg margin, not additive) and captured real error-code fixtures now locked into `tests/test_fyers_span_margin_certified.py`. I did not perform this certification myself; citing it here because `config/config.yaml`'s `capital_policy: CERTIFIED` setting depends on it and a future reader needs to know it's backed by real evidence, not a default.

### D13. `vwap_equal_weight_fallback: false` in the legacy system
**Source**: `README.md`, `config/config.yaml` comments.
**What was chosen and why** (as documented): FYERS's live index quote returns `volume: 0`/`atp: 0` (no broker-provided VWAP), but the historical/candle endpoint returns genuine per-candle volume — so VWAP is computed as a true volume-weighted calculation from candles, with the old equal-weight approximation kept only as an explicitly-disabled fallback. If a feed ever reports no volume, the system is designed to refuse to trade rather than risk capital on an approximated reference. I did not verify this myself this engagement; citing it as documented.

### D14. Legacy system risk thresholds (`max_mtm_loss: 6000`, `daily_loss_limit: 6000`, `breakout_body_ratio: 0.60`, `lots: 1`)
**Source**: `config/config.yaml`.
**Honest gap**: I do not have direct knowledge of the backtesting or quantitative analysis (if any) that produced these specific numbers — I did not set them and have not located a dedicated doc justifying them by number (as opposed to the qualitative VWAP/margin decisions above, which are documented). If this history matters, check `reports/` (contains historical campaign JSON/MD pairs from replay qualification runs) and `bujji/qualification/`/top-level `qualification/` output before assuming these were arbitrary — but I'm not asserting they weren't, only that I haven't verified it.
