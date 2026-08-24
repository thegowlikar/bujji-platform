# Paper session runbook

For a **clean, live-priced paper session** — Bujji seeing the real market,
deciding, and recording, with execution neutered by
`bujji.broker.guard.disable_live_execution()`.

This runbook does not authorize a session. It describes how to determine
whether one should be run, and how to judge it afterwards. Every command here
is read-only.

> **This runbook is not Gate 1.** Gate 1 — its timer, harness, comparator,
> token receiver, no-trade isolation and output locations — is frozen and is
> not touched by anything below. A paper session is what may become possible
> *after* Gate 1 reports, and only if it does.

---

## 0. Preconditions that are not this runbook's to give

| Precondition | Who decides |
|---|---|
| A valid FYERS token exists | operator, by their own refresh |
| Gate 1 has reported and the feed is usable | Monday's measurement |
| The branch has been reviewed and deployed | operator |
| Capital and risk limits are correct | operator |

Nothing in this repository may set these, and no tool here should be read as
having done so.

---

## 1. Before: run the readiness gate

```bash
cd /opt/bujji/work-m4 && PYTHONPATH=. python tools/paper_session_readiness.py --expect-sha <intended-commit>
```

It checks four things and refuses on any of them:

1. **IDENTITY** — the deployed checkout is clean, the verified branch is
   clean, and the checkout is the commit the operator intended. Omitting
   `--expect-sha` yields UNKNOWN, not PASS: nothing verified the identity.
2. **INHIBITION** — no unit can start an order-capable session on its own.
3. **CONFIGURATION** — `shadow_mode` is *literally* `true`. A string `"true"`
   is refused, because the runner derives defined-risk-only from a literal.
4. **EVIDENCE** — the previous session's package replays to the required
   level.

Exit codes: `0` READY, `4` PENDING_EVIDENCE (something was UNKNOWN), `6`
REFUSED (something FAILED), `1` config error, `2` the tool itself failed.

**UNKNOWN is never PASS.** A check that could not run has not been satisfied.

---

## 2. After: verify the evidence package

```bash
cd /opt/bujji/work-m4 && PYTHONPATH=. python tools/decision_replay_verifier.py <session-package-dir>
```

It reports the strongest replay level the package *demonstrates*, by
recomputing — not by reading back what was written down:

| Level | Means |
|---|---|
| 0 `NO_EVIDENCE` | nothing readable |
| 1 `INPUT_INTEGRITY` | records parse and agree on session, order, identity |
| 2 `ANALYTICAL_REPRODUCIBILITY` | each selection resolves to the assessment it acted on, and the two agree about the regime handed over |
| 3 `ELIGIBILITY_REPRODUCIBILITY` | the recorded regime re-derives the recorded candidate set and selection |
| 4 `FULL_DECISION_EQUIVALENCE` | the recorded inputs re-derive the recorded orders |

Levels are **strictly ordered**. Demonstrating level 3 while level 2 was never
attempted earns level 1 — the tool will say so, and will always name the
obstacle to the next level.

---

## 3. Acceptance criteria for a clean paper session

A session is **accepted** when all of the following hold. Anything else is a
session that ran, not a session that passed.

**Safety**

- [ ] No real order was placed; the broker guard was active for the whole run.
- [ ] The session reached a terminal state and produced a verdict.
- [ ] No orphan exposure remains unresolved, and none was left UNKNOWN.
- [ ] Every exit converged through the one closure path.

**Evidence**

- [ ] A durable session evidence package exists.
- [ ] `decision_replay_verifier` reports **level 2 or better**.
- [ ] Every selection carries an `analytical_snapshot_ref` that resolves.
- [ ] Every selection carries a candidate record accounting for every declared
      shape — including on a no-trade day, where it is the *only* evidence of
      what Bujji considered.

**Discipline**

- [ ] At most one strategy was locked.
- [ ] Any refusal is explained by a reason code, not only by prose.
- [ ] The session's exit code matches its verdict.

**Not acceptance criteria, deliberately**

- Profit or loss. A paper session is judged on whether it behaved correctly
  and can explain itself, never on whether it made money. A profitable session
  that cannot replay has failed.
- Number of trades. Zero trades is a valid outcome, and for a premium seller
  it is the common one.

---

## 3a. Gate 1 scope, as of 2026-08-25

Tomorrow measures continuous capture from the opening-ready subscription set
during **09:15:00–15:40:00 IST**. Reconnect recovery and subscription capacity
are intentionally unmeasured and deferred to dedicated no-trade live-venue
experiments.

The claim that run may make is exactly:

> "Bujji captured every SDK callback it received from the opening-ready
> subscription set during 09:15:00–15:40:00 IST."

It may **never** claim every exchange tick was delivered by FYERS. The corpus
records what arrived, not what existed — and no measurement taken from inside
the receiver can distinguish "the venue sent nothing" from "the venue sent
something we never saw".

**15:40 is the F&O close, not 15:30.** 15:30 is the cash close; Bujji trades
F&O (NSE circular 2026-05-30, effective 2026-08-03). Anything reasoning about
Bujji's own instruments wants `FO_MARKET_CLOSE` from
`bujji/market_calendar.py`, which is the single authority — the Gate 1
orchestrator now derives its window from it rather than declaring a literal.

## 4. What a level-2 ceiling means

Today a session that does everything right reaches **level 3** at best, and
level 4 is unreachable for a structural reason worth stating plainly: the
option chain that strikes were chosen from is not part of the evidence
package. Strikes cannot be re-derived from a book nobody wrote down.

Closing that is a decision about evidence volume, not a defect to patch
quietly — a full chain snapshot per entry is a large artifact, and whether it
is worth keeping is the operator's call.
