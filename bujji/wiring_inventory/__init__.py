"""Wiring inventory -- what actually runs, and what only exists."""
__dormant__ = (
    "Operator tool, run on demand via scripts/wiring_inventory.py; deliberately "
    "not on any systemd timer. Declared 2026-08-17 so this package practises "
    "the discipline it measures."
)
from .inventory import build_inventory, InventoryReport, PackageStatus  # noqa: F401
