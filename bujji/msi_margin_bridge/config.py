"""Margin Bridge & Capital Fidelity config — Series 97.

Every constant here is a disclosed, standard retail-margin heuristic
(the kind of round-number rule of thumb a retail broker's own margin
calculator page commonly cites for undefined-risk index option
positions), never fit to replay P&L. For DEFINED_RISK constructions no
approximation constant is needed at all -- max loss is a mathematical
fact of the structure (wing width minus credit received, or the debit
paid), computed exactly in engine.py.
"""
from __future__ import annotations

# Undefined-risk (naked short / straddle / strangle / ratio / covered /
# synthetic) approximation: the higher of --
#   (a) a flat percentage of notional exposure (spot x lot_size), and
#   (b) a multiple of the premium collected.
# Whichever is larger is used, matching the common retail-broker
# convention of quoting margin as "the greater of X% of contract value
# or Nx premium".
UNDEFINED_RISK_NOTIONAL_PERCENTAGE = 0.15
UNDEFINED_RISK_PREMIUM_MULTIPLE = 3.0
