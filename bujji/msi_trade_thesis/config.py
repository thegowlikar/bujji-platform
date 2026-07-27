"""Trade Thesis Engine config — Series 92. Structural scoring
thresholds only -- never tuned against replay outcomes or historical
performance."""
from __future__ import annotations

# Conviction is a simple 0-3 point rank (Deliverable 4), mapped to
# NONE/LOW/MODERATE/HIGH -- a declared, disclosed rubric, not a fitted
# score:
#   +1 if 2 or more domains support the chosen thesis
#   +1 if zero domains conflict with it
#   +1 if Consensus's own consensus_level is MODERATE_CONSENSUS or stronger
CONVICTION_RANK_TO_LEVEL = {0: "NONE", 1: "LOW", 2: "MODERATE", 3: "HIGH"}

MIN_SUPPORTING_DOMAINS_FOR_CONVICTION_POINT = 2
