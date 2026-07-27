"""Live Market Event Engine — BUJJI Engineering Series 75 (LMEE v1).

Lives at `bujji/live_market_events/`. Consumes the Observation Contract
(Series 73A/73B/73C) and answers only "what objectively changed between
two observations" -- never what the change means. This is the first
layer that reasons across TIME, but never across MEANING.

Market Events describe changes. They never describe meaning.
"""
