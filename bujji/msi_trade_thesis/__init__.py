"""bujji.msi_trade_thesis — Trade Thesis & Position Intent (Series 92).

Sits BETWEEN MSI and Strategy Selection Foundation:

    Observation -> Events -> Episodes -> MSI -> Trade Thesis
        -> Strategy Selection Foundation -> Trade Construction -> Portfolio Construction

Answers "what opportunity exists, why, and what should a position
express" -- a market thesis, never a strategy choice. This package
never picks strikes, never constructs trades, never scores
profitability, and never ranks strategy families (that remains
Strategy Selection Foundation's job, unchanged, downstream of this
one)."""
