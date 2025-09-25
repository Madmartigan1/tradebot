# EMA TradeBot (beta)

A minimal crypto **EMA crossover trade bot** that listens to Coinbase Advanced WebSocket tickers and places trades via the REST API. EMA is the **captain**; RSI and MACD act as **advisors** that can veto bad entries/exits. State (P&L, fills processed, daily spend) is tracked locally under `.state/`.

## Features (beta)
- Streaming **EMA crossover** signals on multiple products (per-asset EMA overrides supported).
- **Advisor vetoes:** RSI (overbought/oversold) and MACD histogram momentum.
- **Maker-prefer** limit orders (post-only) with per-asset basis-point offsets, or market orders.
- **Daily spend cap** and **cooldown** per product.
- **Dry-run mode** (default) that logs trades without sending orders.
- **Fills reconciliation** on startup to keep local P&L in sync.
- **Session footer** logs P&L and runtime to `.state/trade_log.txt`.

## Repo layout
- None

## ⚠️ Disclaimer:
This bot is intended for educational and experimental purposes only. It is not financial advice and will not guarantee profit. Use it at your own risk.
Always do your own research, monitor your trades, and configure the system to match your risk tolerance.
Past performance is not indicative of future results. Trade responsibly.
