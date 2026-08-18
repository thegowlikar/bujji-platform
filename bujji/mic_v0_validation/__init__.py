"""bujji.mic_v0_validation — Phase 20.1B.

Answers, using ONLY real market data: when `bujji.mic_v0` says TREND,
does the market actually behave differently from when it says RANGE?
Never uses strategy P&L — that would confound "is the classifier
accurate" with "is a strategy profitable," two different questions
(see docs/PHASE_20_RESEARCH_CAMPAIGN_CHARTER.md's own finding on this).

No broker import, no execution surface, no strategy vocabulary.
"""
