# Phase 20.0 — Historical Data Capability Report

**Bujji Research Campaign v1.0. Document only — no code written in this phase, per charter.**
**Audit date: 2026-08-16.**

This report answers the charter's own question directly: *what truth do we actually possess?* Every claim below is verified against the real database on the VPS or against prior findings already documented in this repository (`docs/DATA_ACQUISITION_SPRINT_A.md`, `docs/BUJJI_RE_BASELINE_ASSESSMENT.md`) — none of it is assumed.

---

## 1. Historical Data Capability

| Data | Available | Coverage | Resolution |
|---|---|---|---|
| NIFTY spot (underlying) | **YES** | 1998-05-04 → 2026-08-13 (daily); 2017-07-17 → 2026-08-14 (5-min) | Daily: 7,041 rows. 5-min: 168,194 rows |
| India VIX | **YES** | 2008-04-17 → 2026-08-13 (daily); 2017-07-17 → 2026-08-14 (5-min) | Daily: 4,524 rows. 5-min: 168,157 rows |
| NIFTY futures (continuous) | **YES** | 2018-01-02 → 2026-08-13 (daily); 2018-02-01 → 2026-08-14 (5-min) | Daily: 2,131 rows. 5-min: 157,966 rows |
| **NIFTY options — historical OHLC/OI (any date before 2026-08-14)** | **NO** | **Zero rows.** All 162,150 option rows in `historical_observations.db`, across 2,190 distinct contracts, are timestamped **2026-08-14 only** — a single day's live verification capture, not an accumulated history. | N/A |
| **NIFTY options — historical bid/ask (any date)** | **NO** | Never captured, at any date, for any contract. See §3. | N/A |

**This is the single most important finding of this audit.** Three separate instruments (spot, futures, VIX) have 8-26 years of real history. The options chain — the actual instrument the candidate strategy (ORB VWAP short straddle) trades — has **one calendar day** of historical data in the entire system.

## 2. Why: FYERS Cannot Provide Historical Options Data At All

This was already discovered and documented before this campaign, in `docs/DATA_ACQUISITION_SPRINT_A.md`:

> FYERS (`fyers_historical`/option-chain API, already connected in this project): **No** — confirmed via both official community reports and a direct capability check: no option-chain endpoint (historical or live) exists; historical OHLC is limited to *currently active* contracts, not expired ones.

This is not a gap Bujji failed to capture — it is a hard capability boundary of the broker relationship. FYERS' historical API simply does not serve expired option contracts. The 2,190-contract, single-day capture in the store exists because it was captured *live*, on 2026-08-14, as that day happened — there is no way to retroactively obtain the same data for any earlier date through this broker.

**Viable alternative already identified (same prior sprint):** NSE's own public F&O Bhavcopy (`nsearchives.nseindia.com`) is free, requires no account, and was verified against two real days (2026-07-21, 2026-07-22) — cross-validated to the last decimal against FYERS' own spot close for the same dates. It provides, per contract, per day: `OpnPric`, `HghPric`, `LwPric`, `ClsPric`, `SttlmPric`, `OpnIntrst`, `ChngInOpnIntrst`, `TtlTradgVol` — **real daily OHLC + OI + volume, not just a closing print.** NSE's archive is reported to extend back several years, though this has only been spot-checked on two days; a full multi-year download has never actually been performed. **Only 4 bhavcopy files currently exist on the VPS** (2026-07-27 through 2026-07-30) — the local archive is not a historical corpus yet, it is four incidental days.

**No bid/ask, no depth, no intraday timestamp exists in NSE Bhavcopy** — it is one EOD snapshot per contract per day. This caps any bhavcopy-based backtest at **Execution Level C (close-price simulation)** under the charter's own framework, unless spread is separately modeled on top of it (which would make it Level B — modeled, not historical).

## 3. Execution Reality Level — Formal Classification

Per the charter's own A/B/C framework:

```
Historical Bid/Ask:      NOT AVAILABLE  (confirmed — never captured, no source exists)
Historical Depth:        NOT AVAILABLE
Historical Ticks:        NOT AVAILABLE
Historical OHLC (options): NOT AVAILABLE from FYERS. AVAILABLE from NSE Bhavcopy (untested at scale — 4 days on disk).
Historical OI/Volume:    AVAILABLE from NSE Bhavcopy, same 4-day caveat.

Backtest realism level for any full historical run: C
  (close-price simulation only — the charter's own words: "Never for capital decisions.")

To reach Level B (modeled, documented): requires (a) downloading a real multi-year
Bhavcopy archive (not yet done — only 4 days on disk), and (b) a documented spread
model calibrated against real observed spread (see §4 — also not yet possible).

Level A is not reachable with any currently accessible, individually-licensed data
source. TrueData explicitly bars individual/retail licensing. Global Datafeeds
requires a paid subscription never established. This is a real, structural ceiling,
not a to-do item.
```

## 4. Forward/Live Capture Capability — A Second, Independent Risk

This concerns Phase 20.1's calibration rule directly (20 live sessions or 400+ option-leg minutes needed to move execution-model confidence off LOW). Two already-documented, real production incidents change what should be expected from Monday's validation and from ongoing capture through the campaign:

- **`FyersTickFeed` defaults to `litemode=True`**, which delivers `{"symbol", "ltp", "type"}` only — **no bid, no ask, no volume, no OI, no depth, in the tick stream itself.** Full mode's behavior for option symbols specifically has never been live-verified in this repository.
- **On 2026-07-28, a live session received exactly one real tick all day** for `NSE:NIFTY50-INDEX` — lite mode fires only when raw LTP changes, and an index's LTP frequently doesn't produce a change-triggering event.
- **On 2026-07-29, ticks stopped entirely for ~7.5 trading hours** after two ticks pre-open, while `is_connected` stayed `True` and `reconnect_count` stayed `0` the whole time — a silent failure with no honest signal that anything was wrong, root-caused to the underlying SDK's reconnect path (`on_close` never fires when `reconnect=True`).
- **REST option-chain bid/ask/IV extraction has never been implemented or live-verified** in this codebase — only OI extraction (`oi`/`prev_oi`/`oich`) has been built and confirmed. Whether Monday's capture can obtain real bid/ask for the ATM±5 legs at all, via any path, is currently unverified, not merely uncalibrated.

**Consequence for the charter's calibration rule:** "20 live captured sessions" assumes each session actually produces usable tick density. Given a demonstrated history of near-total silent failure under the exact same feed mechanism this campaign will rely on, that assumption needs to be tested, not presumed. `TickSilenceWatchdog` already exists in the codebase (built for exactly this failure mode) and must be wired into whatever eventually becomes `capture_session.py` from the first live run, not added after a repeat incident. `MicrostructureAggregator` (Phase 19.20.2) has never yet been connected to a real `FyersTickFeed` instance — Monday will be the first time these two already-built, separately-tested components meet real market data together.

## 5. Contract-Level Liquidity — Preliminary Signal

Not yet a full feasibility audit (that requires the multi-year Bhavcopy archive from §2, which doesn't exist locally yet), but a real signal from the 4 bhavcopy days already on disk: a material fraction of listed NIFTY F&O contracts on any given day report `TtlTradgVol=0` — i.e., zero trades occurred in that contract that day. Far-OTM and far-dated contracts are disproportionately affected. This directly confirms the concern raised earlier in this campaign's design: a backtest that doesn't check contract-level liquidity before counting a "trade" risks including legs that had no real counterparty on the day in question. The full Report A/B/C liquidity audit (charter §20.0) cannot be completed until a real multi-year archive is downloaded — this is listed as a blocking prerequisite in §6.

## 6. What Must Happen Before Phase 20.1/20.2 Can Proceed

1. **Download a real multi-year NSE Bhavcopy archive** (not the 4 incidental days currently on disk) covering at minimum the walk-forward windows the charter specifies (2023 through 2026). This is a data-acquisition task, not a code-architecture task, and it directly gates whether Phase 20.2's "300+ trades" backtest gate is even reachable — right now it is not, because there is no historical options data to backtest against beyond four days.
2. **Explicitly decide and document the Execution Level the backtest will run at** (Level C, or Level B with a disclosed, calibratable spread model) — given §3's findings, Level A is not available and should be removed from consideration rather than aspired to.
3. **Wire `TickSilenceWatchdog` into the first real capture session by design, not as a fix after a repeat of the 2026-07-29 incident** — the failure mode is already proven twice on this exact feed class.
4. **Live-verify, on the first real session, whether REST option-chain bid/ask is obtainable at all** — this is currently an open question, not a known capability, and the entire premise of calibrating a Level B spread model depends on the answer.

## 7. Bottom Line

The underlying (spot/futures/VIX) data foundation is genuinely strong — years of real history, multiple resolutions, already validated. The options data foundation — the actual instrument any options-selling strategy trades — does not exist historically beyond one day, for a structural reason (FYERS doesn't serve it), not an oversight. A viable path exists (NSE Bhavcopy) but has only been spot-checked, never built into an actual archive. Separately, the live capture mechanism this campaign will depend on for forward calibration has a real, twice-documented silent-failure history under the exact feed configuration currently in use.

None of this means the campaign should stop. It means Phase 20.1's Execution Reality Engine should be built assuming **Level C initially, Level B as an explicit, calibration-pending upgrade** — and Phase 20.2's backtest gate cannot honestly be attempted until the Bhavcopy archive in §6.1 exists. This report is the "what truth do we actually possess" answer the charter asked for. The honest answer is: less than the charter's Phase 20.2 examples assumed, in a way that changes what Level 20.1-20.2 can build first.
