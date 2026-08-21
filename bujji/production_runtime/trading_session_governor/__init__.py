"""BUJJI Options OS v3, Gate V1.1 -- Trading Session Governor.

A thin behavioral control layer on top of the completed Shadow
Trading OS (F.0-F.5 + V.0). Owns SESSION TRADING DISCIPLINE only:
select one strategy per day from the existing regime/strategy
infrastructure, deploy it once, and enforce hard exit limits
alongside D.4's own lifecycle intelligence. It owns no market
analysis, no risk calculation, no order execution, no MTM
calculation, and no lifecycle intelligence -- those remain exactly
where F.0-F.5 already put them.
"""
