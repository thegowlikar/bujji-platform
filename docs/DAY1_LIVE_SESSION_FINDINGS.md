# Day 1 Live Shadow Session — Findings

**Date: 2026-07-28. Branch: `v1.0-shadow` @ `cf08fdd385e6212d4e251614d0bba99504b7ea2a`
(clean, confirmed by the session's own pre-market checklist). Session
window: 06:43:35 IST start → 15:30:00 IST clean shutdown (full trading
day, market open to close).**

This is the first time any part of this system has run against a real,
authenticated FYERS session. Treat every number below as real evidence,
not a projection.

## 1. What worked — for the first time, ever

- **Real FYERS authentication**: `broker.connect()`'s real `profile`
  API call succeeded on the first attempt.
- **Real WebSocket connection**: connected in ~1.9s, subscribed to
  `NSE:NIFTY50-INDEX`, and a real tick (23,995.95) arrived within the
  15-second mandatory-check window.
- **Real quote and option-chain connectivity checks** both passed.
- **Disk (92.6% free), journal writability, process lock, and the
  market-calendar check** (correctly identified 2026-07-28 as a real
  trading day via weekday arithmetic) all passed.
- **Shadow safety held perfectly, all day, with zero exceptions**:
  execution module never imported, zero order-placement calls possible
  by construction, verified structurally (not just by outcome).
- **The Sprint 112 freshness-pause mechanism worked exactly as
  designed**: rather than fabricate a decision on stale data, it
  correctly paused 35 times in a row over the full session, never once
  producing a guessed or degraded decision.
- **Clean shutdown at market close**, real end-of-day report and health
  snapshot generated, real journal written (`operator_journal.jsonl`,
  8 entries).

## 2. The critical finding: zero decision cadences completed

Every single scheduled cadence (every 15 minutes, 06:58:40 through
15:28:49 — 35 attempts) was skipped with the same real, honest reason:

```
cadence_skipped: decision generation paused for 2026-07-27:
mandatory input(s) STALE -- underlying_tick: age=901.4s >= STALE
threshold 900.0s; option_chain: age=55720.4s >= STALE threshold 900.0s
```

**Net result: BUJJI produced zero observations, zero events, zero
theses, zero strategy selections, zero shadow trades today.** The
session completed safely and honestly — but it did not produce the
evidence this whole effort exists to accumulate. That is the real,
first-priority problem to solve before Day 2.

## 3. Root cause investigation — real, timestamped evidence

**Only one real websocket tick was ever received the entire day** —
the single tick captured during the pre-market checklist at 06:43:39
(23,995.95). No further tick updated the price after that, for the
remaining ~8.75 hours of the session.

Timeline, real log evidence:
- `06:43:39` — first (and only) real tick received.
- `06:58:40` — first staleness check, already shows `age=901.4s`,
  meaning **nothing arrived in the very first 15-minute window** — this
  predates any connectivity problem.
- `07:47:07`–`07:47:29` — a real, separate incident: 5 WebSocket
  handshake failures (`502 Bad Gateway`, Cloudflare-fronted), each
  followed by a successful reconnect (`tick_feed_connected`) within ~2
  seconds. This is a genuine transient FYERS/Cloudflare-side outage,
  confirmed via the FYERS SDK's own `fyersDataSocket.log`.
- **Ticks did not resume after this reconnect either.** Since staleness
  was already 901s+ *before* 07:47, this incident is a real but
  *separate* event — not the root cause of the all-day silence.
- `15:30:00` — clean shutdown, as designed.

### Leading hypothesis (code-grounded, not yet live-confirmed)

Read directly from the installed `fyers_apiv3` SDK source
(`FyersWebsocket/data_ws.py`), not assumed:

`FyersTickFeed` subscribes with `litemode=True`. For an INDEX symbol,
the SDK's lite-mode update path (`data_type == 76`) only invokes the
message callback when the raw encoded price differs from the
previously stored value:

```python
elif topic_id in self.index_sym:
    value = struct.unpack(">i", data[offset:offset+4])[0]
    if value != self.resp[self.index_sym[topic_id]][self.index_val[0]] and value != -2147483648:
        # ...only here does the callback fire...
```
(`self.index_val[0]` is `"ltp"` — the *only* field lite mode compares
for an index.)

The SDK's **Full mode** path (`data_type == 85`) has the same
"only-if-changed" gate, but checks it across *every* field in
`index_val` — including `exch_feed_time`, a timestamp that changes on
essentially every real broadcast packet regardless of whether price
moved. Lite mode has no equivalent field to fall back on.

**This is a real, plausible, code-grounded explanation for why lite-mode
index updates could go silent far more often than expected** — but it
is not proof. It cannot be confirmed without capturing raw messages
during a real live session (market was closed by the time this
investigation happened), and it does not, by itself, obviously explain
*eight straight hours* of total silence rather than intermittent gaps.
**Status: strong lead, not a confirmed root cause.**

### Secondary finding — reconnect telemetry undercounts real reconnects

The session's own `reconnect_count` metric read **0** all day, despite
5 real, logged reconnects during the 07:47 incident. Root cause,
confirmed by reading the code: `LiveShadowOperator`'s reconnect counter
is wired to `FyersTickFeed`'s `on_disconnect` hook, which only fires
from *our* `on_close` callback wrapper — and the real session log shows
`tick_feed_error` → `tick_feed_connected` pairs during the 502 incident
**with no `tick_feed_closed` line ever appearing**. The SDK's own
internal retry logic appears to absorb this specific failure mode
(handshake-level, not an established-connection close) before it ever
reaches our `on_close` callback. A real, minor observability gap —
reconnects genuinely happened; our own metric just didn't see them.

## 4. Operational metrics — real, measured

```
uptime:              31,581.2s (~8.77h, full session)
peak memory:         78,184 KB
cpu (user+sys):      6.933s + 0.996s over the whole session
disk free:           92.5% (end of day)
dropped_ticks:       0
duplicate_observations: 0
api_retry_count:     0
api_dropped_requests: 0
journal_health:      HEALTHY
```

No memory growth concern, no disk concern, no duplicate-tick or
duplicate-decision issue (trivially true — zero decisions were made to
duplicate).

## 5. One operational hiccup, unrelated to the above

`python` (bare) is not installed on the server — only `python3` /
the project's own venv interpreter. The run command needed
`/opt/bujji/.venv/bin/python` explicitly. Trivial, already worked
around, worth remembering for the next session's exact command.

## 6. Success criteria (per the original sprint's own template)

| Criterion | Result |
|---|---|
| Completed a full live market session | ✔ Yes, 06:43–15:30 |
| Never placed an order | ✔ Yes, structurally impossible, confirmed all day |
| Never crashed | ✔ Yes, clean shutdown |
| Produced deterministic decisions | — N/A: zero decisions were produced to evaluate |
| Recorded every assessment | ✔ Trivially: zero assessments occurred, zero were lost |
| Generated complete end-of-day evidence | ✔ Yes — report, journal, health snapshot all real and complete, honestly reflecting an empty day |
| Explained every operational anomaly | ✔ This document — the 502 incident and the tick-silence gap are both real-evidence-grounded, not hand-waved |

## 7. Recommendation

**Pause and fix this specific engineering defect before Day 2** — not
because anything is unsafe (shadow mode held perfectly), but because
running another live day without addressing tick silence would just
produce another zero-evidence day. This satisfies the stated rule:
observed live ✔, reproducible (needs one more live confirmation) —
pending, root cause understood — partially (strong lead, not proven),
deterministic fix — candidate identified (stop using lite mode for the
index subscription), no strategy tuning — confirmed, this is purely
`bujji/broker/fyers_ws.py` tick-ingestion behavior, zero decision logic
involved.

**Concrete next step, not yet done**: before the next live session,
either (a) instrument a short, targeted live capture of raw index
messages during market hours to confirm or rule out the lite-mode
hypothesis definitively, or (b) switch the index subscription to full
mode and observe whether ticks flow continuously — treating that as the
live confirmation itself. Either path stays inside the "observed live →
reproducible → root cause understood → deterministic fix" chain rather
than guessing blind.

## 8. Fix deployed (2026-07-28, same day, market closed) — pending live
confirmation

Implemented option (b) above. Changes, both additive/backward-compatible:

- `bujji/broker/fyers_ws.py`: `FyersTickFeed.__init__` gained a new
  `litemode: bool = True` parameter (default preserves EXACT existing
  behavior for every current caller, including the legacy production
  stack's own `app.py` call site, which passes no `litemode` argument
  and is therefore unaffected). The hardcoded `litemode=True` passed to
  the underlying `data_ws.FyersDataSocket` constructor is now
  `litemode=self._litemode`.
- `run_live_shadow.py`: the live session's own `FyersTickFeed`
  construction now explicitly passes `litemode=False`.

**Verified before deployment**: syntax clean, full regression suite
2840/2840 passing (unchanged from pre-fix), recorded-day mode
(`--day`) re-run end-to-end successfully. Zero decision-logic files
touched.

**Status: deployed, NOT yet confirmed live.** This satisfies
"deterministic fix, no strategy tuning" from the stated rule; it does
NOT yet satisfy "reproducible" / "root cause understood" to completion
— that requires observing continuous real ticks during an actual live
market session, which was not possible today (market closed). This is
the first thing to check at the start of the next live session: does
`underlying_tick` staleness stay near zero throughout the day instead
of growing monotonically from a single first tick, as it did today.
