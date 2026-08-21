"""Market State Builder -- Shadow Campaign v2 Phase 3B.

Converts live MarketSnapshot observations into real market-understanding
objects (Observation -> MarketEvent -> Episode -> MarketStructure/
PriceStructure/ParticipantPositioning assessments), reusing existing,
unmodified engines throughout: market_observation, live_market_events,
market_episode, options_observation, msi_market_structure,
msi_price_structure, msi_participant_positioning.

Only builds market UNDERSTANDING -- no Trading Brain, Strategy
Selection, Trade Intent, Execution, Position Management, or Risk
decisions live here or are imported here.

NOT wired into ShadowSessionRunner yet -- standalone pipeline only,
per this phase's own instruction to validate independently first.
"""
