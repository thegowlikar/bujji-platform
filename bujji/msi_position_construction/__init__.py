"""bujji.msi_position_construction — Position Construction Intelligence
(Series 95).

Sits BETWEEN Strategy Selection and Series 90's real Trade Construction:

    ... -> Strategy Selection -> Position Construction -> Trade Construction
        -> Portfolio Construction

Translates a SELECTED strategy family (plus the thesis and expression
that justified it) into a concrete CONSTRUCTION PLAN -- which shape
(single leg, vertical spread, straddle, iron condor, ...), which
expiry philosophy, which strike philosophy, which wing philosophy, what
risk/payoff geometry, and how adjustment-friendly the shape is. This is
a PLANNING layer: it never touches a real option chain, never solves
an IV, never computes a real Greek value -- it operates one level of
abstraction earlier than Series 90's Trade Construction, which remains
the sole place real strikes/expiries/premiums are ever chosen. Every
policy constant that Series 90 already declares (expiry DTE window,
per-family delta targets, wing-width policy, defined/undefined-risk
family classification) is imported and reused here directly, never
re-declared."""
