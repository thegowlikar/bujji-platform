# Phase 17I.7 — 10-Session Market Reality Capture Campaign: Readiness Review

**Status: AUDIT + PLANNING ONLY. No code, schema, or taxonomy changes.**

Grounded in the real, live-verified state as of 2026-08-13: Phase 17I.6's
5-cycle live run (15/15 accepted), 17I.6.1's raw methods, and 17I.6.2's
real `tt`/`ltt` timestamp finding (both live, dated captures, not
inference).

---

## 1. Does a 10-session campaign correctly follow the MIC vision?

Yes. It stays entirely inside Reality — accumulating evidence and
measuring its own trustworthiness, not interpreting it. No step below
proposes a signal, a classification, or a regime label.

## 2. Is Spot + Futures + VIX still the correct minimum triangle?

Unchanged from every prior review in this chain: yes. Basis (spot vs.
futures), aggregate OI trend, and volatility level are all directly
observable from this triangle; option chain remains correctly excluded
pending its own unresolved identity-model decision (17I.3), not
reconsidered here.

## 3. What exactly should be measured

**Capture reliability**
- Successful vs. attempted cycles, per instrument, per session.
- Real cadence drift: actual inter-write spacing vs. the nominal 60s.
- Consecutive-failure streaks per instrument (a lone miss vs. a run of
  misses are different findings).
- `CaptureLifecycleTracker` event count and duration per session (time
  between an opened condition and its `RECONNECT_RECOVERED`, if any).

**Storage integrity**
- Total `raw_observations.jsonl` growth vs. the arithmetic expectation
  (cycles × 3, adjusted for any logged per-instrument misses).
- Duplicate count in the accepted store — expected 0 under normal
  single-launch-per-session operation.
- Rejection count — expected 0; any non-zero value is a real regression
  signal, not a tolerance band.
- Certification linkage stability — confirm every record's
  `certification_ref` across all 10 sessions still points at the same
  three artifacts already on disk (no unexpected new certification
  artifact appearing mid-campaign).
- No malformed/torn lines (`EventStore.read_events_with_diagnostics`
  reports zero skipped lines across the full campaign).

**Market observation metrics** — descriptive only, explicitly not signals:
- Spot/futures basis range across the 10 sessions.
- Raw OI trajectory (the sequence of readings) and OI null-rate (how
  often the best-effort depth call comes back without one).
- VIX level range across the 10 sessions.
- Volume field presence/null-rate.

## 4. Hidden architectural risks

**One real, dated, concrete risk — not hypothetical.** A 10-trading-session
campaign starting today (2026-08-13, Wed→ actually Thursday) runs:
Aug 13, 14, 17, 18, 19, 20, 21, 24, **25**, **26**. **Session 9 (Aug 25)
is the real, already-confirmed expiry date of the current near-month
NIFTY futures contract** (`NSE:NIFTY26AUGFUT`, `expiry_epoch` verified
directly against the live NFO CSV in Phase 17I.5). Session 10 (Aug 26)
is the first session after rollover.

Concretely, this means:
- `resolve_nearest_future()` is called fresh at the start of each new
  session process — so session 10 will correctly resolve
  `NSE:NIFTY26SEPFUT` instead of the expired August contract. This is
  the *correct* behavior, already structurally proven (no code change
  needed) — but it must be expected, not discovered as a surprise: the
  captured futures symbol legitimately changes mid-campaign.
- Session 9 itself (the expiry day) is not an ordinary trading day for
  this contract — OI/volume/basis dynamics around expiry are
  structurally different (settlement effects). This should be measured
  and reported descriptively (per §3), not treated as anomalous data to
  discard, and not interpreted.

**Real, not yet measured at scale**: the true per-session call volume is
larger than what's been tested. `get_futures_quote()`'s real
implementation makes 2 calls (`ltp` + `depth`); spot and VIX make 1
each — **4 REST calls per 60s cycle**, confirmed in code, not estimated.
Over a full ~6h15m session (09:15–15:30) at 60s that's roughly 375
cycles × 4 ≈ **1,500 calls/session**, ×10 ≈ 15,000 across the campaign.
Only ~5 minutes (20 calls) has actually been tested live so far (Phase
17I.6). Rate-limit behavior at this real scale is genuinely unverified
— the campaign's first full session is also the first real test of
this, not a settled fact.

**Checked and confirmed NOT real risks** (verified against actual code
this session, not assumed):
- **JSONL growth** — ~1,125 lines/session, ~11,250 across the campaign.
  Trivial at this scale.
- **Cross-session duplicates** — `observation_id` is a deterministic
  content hash including the real per-session wall-clock
  `capture_timestamp`; distinct sessions structurally cannot collide.
  Already proven live (17I.6's restart-rehydration check).
- **Certification staleness** — `CertificationGate` reads live per
  query (`cache=False`, confirmed in the actual construction) and has
  no TTL/expiry semantics of its own; a certification once written
  stays authoritative until a newer artifact for the same
  `(instrument, access_method)` pair supersedes it.
- **Tracker lifecycle across sessions** — a fresh tracker per session
  process is the tracker's own documented, intentional design (a
  restart genuinely has no open condition to inherit), not a gap.
- **Session restart mid-day** — already proven idempotent both in unit
  tests and by the real restart-rehydration check in 17I.6's live run.

## 5. Should a campaign manifest be created before execution?

**Yes.** Minimum contents:
- Campaign window (the 10 real dates above, explicitly including the
  Aug 25 expiry/Aug 26 rollover note from §4).
- Instruments and access method (unchanged: Spot/Futures/VIX,
  `direct_sdk_fyers_broker_py`).
- The measurement plan from §3, verbatim, so what gets checked doesn't
  drift session-to-session.
- Success criteria from §6.
- An explicit "not in scope" list (no Memory/Understanding/Intelligence
  work, no option chain, no depth expansion beyond the existing
  futures-OI cross-check).
- A per-session log table (date, start/end IST, cycles completed,
  accepted/rejected counts, tracker events, notable issues) — filled in
  as each session actually runs, so the post-campaign review reads real
  data rather than reconstructing it from raw JSONL after the fact.

## 6. Explicit success criteria

- **Minimum successful capture**: ≥95% of attempted cycles produce all
  three instruments accepted (allows for isolated per-instrument misses
  under the existing honest failure model, not silent tolerance of
  systemic failure).
- **Acceptable gaps**: any gap must be attributable to a real, logged
  cause (rate limit, transient network failure, the expected expiry
  rollover transition) — an unexplained gap is a finding to
  investigate, not a number to accept.
- **Rejected observations**: 0 expected. Any non-zero count is a
  stop-and-investigate signal, not a tolerated rate.
- **Lifecycle events**: any `AUTH_FAILURE`/`DISCONNECT` is acceptable
  *only if* a matching `RECONNECT_RECOVERED` closes it before session
  end — an unclosed condition at session end is a real finding to
  review afterward.
- **Certification failures**: 0 tolerated. Any status other than
  `CERTIFIED_AVAILABLE` for any of the three `(instrument,
  access_method)` pairs at any point stops the campaign for review, not
  something to capture around.
- **Duplicate observations**: 0 expected in the accepted store under
  normal single-launch-per-session operation.

## 7. Should Bhavcopy remain deferred until after the campaign?

Yes, unchanged from the prior review chain — no architectural
dependency, Bhavcopy's own EOD data doesn't carry the same forward-only
urgency as live capture, and diverting attention while this campaign is
the first real test of full-session reliability (per §4) would be the
wrong tradeoff. Revisit after, not during.

## 8. Next milestone after Phase 17I.7

**Not a jump straight to Memory, Bhavcopy, or option chain.** The
correct next milestone is the **post-campaign review and synthesis of
what the 10 sessions actually showed** — real uptime %, real OI
null-rate, real rate-limit behavior at the ~1,500-call/session scale,
and how the Aug 25→26 rollover actually behaved in practice. That
review is what should *decide* between Memory/Bhavcopy/option-chain
next, based on real evidence rather than a pre-committed choice made
before the campaign has run. This mirrors this engagement's own standing
discipline (never skip the review step, never decide the next phase
before the current one's evidence exists) — and directly determines
Bhavcopy's own "after forward capture stabilizes" condition from §7,
which can't honestly be evaluated until this review happens.
