"""Market Perception -- Shadow Campaign v2 Phase 1.

The unified market observation layer: turns read-only FYERS broker
calls into one immutable MarketSnapshot per cycle. Contains NO
interpretation, NO decision logic, and calls NO order/position/margin
broker method -- see models.py and market_data_adapter.py module
docstrings, and tests/test_market_perception_safety.py, which enforces
this at the source level.

Future phases (Intelligence wiring, Trading Brain, Risk Governor,
Virtual Portfolio, Decision Journal, full runner) are explicitly out
of scope for this package until separately approved.
"""
