# BUJJI ENGINEERING LEDGER

Append-only record of engineering decisions and verified events. Newest last.

## 2026-08-18 (pre-charter, session summary)
- Full-tree audit (15 agents + critic): 1,016 uncommitted paths dispositioned; 954 committed in 6 thematic commits; pushed. Zero secrets.
- Ownership sweep: 369 root-owned paths under data/layer0/logs → 0.
- Risk-reality wiring: lot size from instrument master (65, fail-closed; config 75 was stale vs 2026-07-19 audit); whole-book SPAN margin via new bujji/broker/fyers_span_margin.py against the live-certified endpoint — OPERATOR CERTIFIED (providers.margin.type: fyers_certified) on live evidence ₹220,455.74/lot, benefit ₹124,987.20, deterministic; capital: live get_funds probe showed real account ₹0.00 → OPERATOR DECLARED ₹5,00,000 simulated (explicit YAML, loss-limit ratio preserved at 5%).
- Capture reliability: concurrent capture fix (sequential subprocess loop meant the 2nd script could never capture); token pre-flight built + timer 08:45; depth poller timer 09:17; spot-from-chain wired behind its own certification gate + certifier script.
- Safety-guard baseline advanced b148e39 → 360c003 (audited backlog); guards' function preserved.
- Paper-intelligence units: 09:20 beat-collision fixed → 09:27:30; installed; install-test caught missing sandbox dir (226/NAMESPACE) and missing market-open gate (pre-open artifact produced at 05:02 — gate added before any filesystem effect; test artifact quarantined).

## 2026-08-19 (charter day)
- **Chief Engineer charter accepted.** 13-agent forensic recon executed (26-row matrix, 22 findings dispositioned, 4 vision docs validated, adversarial critic). Archived in session task output.
- 08:45 pre-flight FAILED (correct: token expired 06:00). 09:10 shadow campaign died on auth — pre-flight's prediction exact.
- 09:14 operator refreshed token; refresh-file valid but NOT promoted — promoted at 09:15 by CE; shadow campaign restarted; 09:16 fires clean. **Lesson: refresh+promote must be one ritual.**
- 09:17 spot-from-chain CERTIFIED live (5/5, mean 0.062 bps) → first live SPOT rows ever at 09:21 (store, source=fyers).
- 09:17 depth poller first scheduled run (running). 09:22:42 trading session #2: honest NO_TRADE in 12s (regime_source=market_thesis, trend UNKNOWN — single-snapshot limitation confirmed).
- 09:27:30 paper-intelligence first fire REFUSED by own guard vs daily-intelligence all-day lock — structural deadlock; OPERATOR DECISION pending.
- 09:42 live incident: capture running 26 min, zero rows. Diagnosed via strace: **EROFS — layer0_data missing from unit ReadWritePaths** (mount sandbox granted logs/data/.env only; layer0 predates data/ convention). Fixed 09:49, proven live (15 QUOTE rows in 75s). Committed. Same class as 08-18 root-owned failures: layer0 outside data/ escapes every data/-scoped fix.
- **BUJJI_CHIEF_ENGINEER_MASTER_PLAN.md committed** (this ledger's sibling). Roadmap CP-A..CP-F; Gate 3 is the target; execution begins at CP-A remainder + CP-B tick-source fix.
