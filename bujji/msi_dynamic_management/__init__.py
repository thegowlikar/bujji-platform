"""bujji.msi_dynamic_management — Series 109: Dynamic Rolling & Strategy
Transition Intelligence.

Consumes the frozen decision engine (Series 73-108) read-only. Modifies
none of it. Produces six INDEPENDENT roll/adjustment/conversion/exit
decision assessments per position per day, each with an
evidence-computed priority (never a fixed lookup table) and a full
five-part explanation (why / why_now / why_not_later /
why_not_another_roll / why_not_exit). See docs/DYNAMIC_MANAGEMENT.md.
"""
from . import config, taxonomy, models, engine, serialization, query, runner, journal

__all__ = ["config", "taxonomy", "models", "engine", "serialization", "query", "runner", "journal"]
