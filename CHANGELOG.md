# Changelog

All notable changes to this project will be documented in this file.
This format loosely follows *Keep a Changelog* and uses tags for versions.

## [v0.1.0-betaA] - 2025-09-17
### Added
- EMA crossover signal engine with per-product EMA overrides.
- RSI & MACD “advisor” veto gates (overbought/oversold + momentum).
- Maker-prefer (post-only) limit orders with per-asset basis-point offsets.
- Daily USD spend cap and per-product cooldown enforcement.
- Dry-run mode (no live orders) as the default.
- Fills reconciliation on startup to align local P&L with Coinbase.
- Session footer with P&L + runtime; state persisted under `.state/`.
- Config-driven products & risk parameters in `bot/config.py`.
- `APIkeys.env.example`, `.gitignore`, `README.md`, `USAGE.md`, `requirements.txt`.

### Known limitations (beta)
- Some duplication across `tradebot.py`, `constants.py`, `orders.py`, and `persistence.py`.
- `strategy.py` not fully wired; thresholds are defined in `tradebot.py`.
- No unit tests yet for rounding/advisors/P&L accounting.
- Signals are tick-based; a candle-based strategy is planned for a future version.

