"""Shadow Trading Engine config — Series 100.

Deliverable 1 reuse: lot size is imported directly from Portfolio
Construction's own config -- never re-declared.
"""
from __future__ import annotations

from bujji.msi_portfolio_construction import config as prc_config

DEFAULT_LOT_SIZE = prc_config.DEFAULT_LOT_SIZE
