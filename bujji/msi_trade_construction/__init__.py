"""bujji.msi_trade_construction — Trade Construction Foundation (Series 90).

Given a strategy family already selected by
`bujji.msi_strategy_selector` (Series 89), constructs a fully specified
options position -- expiry, strikes, entry reference prices, expected
credit/debit, risk profile -- without ever placing an order. Every
strike/expiry decision is traceable to observable evidence (delta,
distance, expected move, wing width, chain availability, open
interest) -- NEVER to historical profitability. This package contains
no P&L, no backtest, no "best performing" logic of any kind (enforced
structurally by an AST test).
"""
