# Tradebot v1.0.7

[![Latest release](https://img.shields.io/github/v/release/Madmartigan1/tradebot?sort=semver)](https://github.com/Madmartigan1/tradebot/releases)
[![License](https://img.shields.io/github/license/Madmartigan1/tradebot)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.13%2B-blue)](requirements.txt)
![Code size](https://img.shields.io/github/languages/code-size/Madmartigan1/tradebot)
[![Last commit](https://img.shields.io/github/last-commit/Madmartigan1/tradebot)](https://github.com/Madmartigan1/tradebot/commits/main)
[![Open issues](https://img.shields.io/github/issues/Madmartigan1/tradebot)](https://github.com/Madmartigan1/tradebot/issues)
[![Open PRs](https://img.shields.io/github/issues-pr/Madmartigan1/tradebot)](https://github.com/Madmartigan1/tradebot/pulls)
[![Stars](https://img.shields.io/github/stars/Madmartigan1/tradebot?style=social)](https://github.com/Madmartigan1/tradebot/stargazers)

⚖️ **Sharper thresholds and Quartermaster logic — an adaptive fleet navigating both storms and still waters**

Tradebot is an automated crypto trading bot for **Coinbase Advanced**.  
It uses an **EMA crossover** strategy with **RSI/MACD advisors**, plus risk controls like daily caps, cooldowns, and optional stop-loss tolerance.  
By default, it runs on **5-minute candles** with `confirm_candles=3`.

---

## ⚓ The Fleet Metaphor
To make the strategy easier to visualize:

- **EMA → Captain**  
  The EMA crossover is the leader and core signal engine. When the short EMA crosses the long EMA, the Captain gives the order.

- **AutoTune → Navigator**  
  The Navigator studies recent tides — analyzing market “weather” to recalibrate course.  
  When the seas are calm (choppy), it tightens risk controls; when trending, it opens the sails for broader moves.  
  AutoTune dynamically adjusts parameters like EMA confirmation count, RSI thresholds, and MACD bands based on regime votes (uptrend, downtrend, choppy, or blend).

- **MACD → Commodore**  
  The Commodore confirms momentum and trend direction. If the histogram disagrees, the Captain’s signal can be overridden.

- **RSI → Skipper**  
  The Skipper keeps things safe in the short term. If RSI shows overbought/oversold, trades are vetoed even if the Captain/Commodore want action.
  
- **Quartermaster → Take-Profit & Stagnation Officer**  
  The Quartermaster safeguards the fleet’s earnings and tidiness.  
  - **Take-Profit (6%+)**: When profits reach a safe margin, the Quartermaster locks the cargo and sends the ship home — a quick market exit.  

- **Deckhand (24 h, ±2%)**: The “broom” that sweeps idle trades off the deck when they drift aimlessly without momentum, keeping the decks lean and ready for action.
  This ensures capital is recycled efficiently while the Captain (EMA) and Advisors (RSI/MACD) focus on live opportunities.

Together, they form a chain of command:
**EMA (Captain)** gives orders,  
**AutoTune (Navigator)** continuously adjusts the fleet’s heading based on market weather.
**MACD (Commodore)** ensures strategy aligns with the broader trend,  
**RSI (Skipper)** vetoes reckless moves,  
**Quartermaster** secures profits and clears stagnation,  
**Deckhand** keeps the decks clear of idle trades, ensuring the fleet stays agile and battle-ready.

---

## 📖 Documentation
- **Full User Guide (PDF):** [docs/README.pdf](docs/README.pdf)
- More docs:
  - [USAGE.md](USAGE.md)
  - [CONTRIBUTING.md](CONTRIBUTING.md)
  - [CHANGELOG.md](CHANGELOG.md)

---

## ✨ v1.0.7 Highlights
- **Regime voting decoupled from trading timeframe:**
  - New `autotune_vote_interval` lets AutoTune analyze trends on 15-minute candles while trading continues on 5-minute candles.
  - Guarantees more stable regime detection over 18-hour windows.
- **Telemetry upgrade:**
  - AutoTune now runs *after* portfolio reconciliation so it sees real trade KPI data.
  - Advisory “would disable” list now includes reasons like `inactive_3d` or `neg_pnl_3d_bps=-10.0,trades=4`.
- **Candle ordering fix:**  
  All fetched candles are now sorted by timestamp (oldest → newest) to avoid reversed price sequences from Coinbase responses.
- **Cleaner startup sequence:**  
  Reconcile → AutoTune → WebSocket subscription ensures telemetry accuracy before live trading begins.
- **Dry-run hygiene:**  
  Empty KPI telemetry is suppressed automatically in dry-run mode (avoids `no_kpi` spam).
- **Quartermaster module:**  
  Introduces automated take-profit (≥ 6 %) and stagnation (≥ 24 h & ± 2 %) exits, acting before EMA logic to secure gains, clear idle capital, and maintain fleet efficiency.

---

### Upgrade notes
- Ensure `.state/portfolio.json` and `.state/trades.csv` are preserved between versions for accurate P&L and KPI history.
- If you skip multiple days between runs, consider raising:
  - `autotune_lookback_hours` → 48–72  
  - leave `autotune_vote_min_candles=72`
- The EMA/RSI/MACD structures remain unchanged; only AutoTune logic and candle ordering improved.
  
---

## 🔐 Secrets
Copy `APIkeys.env.example` -> `APIkeys.env` and fill your Coinbase credentials.  
Never commit real keys.

---

## 🛠️ Quickstart
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy APIkeys.env.example APIkeys.env   # fill your keys
python .\main.py
```

---

## ⚠️ Disclaimer:
This bot is intended for educational and experimental purposes only. It is not financial advice and will not guarantee profit. Use it at your own risk.
Always do your own research, monitor your trades, and configure the system to match your risk tolerance.
Past performance is not indicative of future results. Trade responsibly.

