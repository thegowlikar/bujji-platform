"""BUJJI Options OS v3, Gate F.2 -- Realistic PaperBroker Market Simulation.

Isolated, pure, deterministic components PaperBroker composes to
simulate a hostile-but-real exchange: fills, slippage, charges, order
lifecycle, and execution reporting. Nothing here places a real order
or touches a live broker -- these are pure calculators/data models
consumed by `bujji.broker.paper.PaperBroker` only.
"""
