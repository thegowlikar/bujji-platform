"""bujji.msi_position_lifecycle — Position Lifecycle Intelligence
(Series 96).

Sits AFTER Portfolio Construction, BEFORE a future Execution layer:

    ... -> Position Construction -> Portfolio Construction -> Position Lifecycle -> (future) Execution

Given a position that was ADMITTED on some entry date, and today's
fresh MSI-derived thesis/portfolio state, determines whether the
position is healthy, deteriorating, an adjustment/exit/harvest
candidate, or whether its original thesis has been invalidated. This
package places no orders and performs no execution -- it produces a
declarative LIFECYCLE PLAN plus, day over day, one real state
classification per still-open position, using only genuinely available
real evidence (thesis comparison, conviction trend, portfolio Greeks,
days-to-expiry). Several classic lifecycle triggers (wing breach,
profit target, theta deterioration, term-structure collapse) have NO
real, replay-safe data source in this codebase -- they are honestly
disclosed as MONITORING REQUIREMENTS only, not evaluated triggers, per
this whole arc's established disclosure discipline."""
