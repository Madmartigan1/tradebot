# Tradebot v1.1.2

[![Latest release](https://img.shields.io/github/v/release/Madmartigan1/tradebot?sort=semver)](https://github.com/Madmartigan1/tradebot/releases)
[![License](https://img.shields.io/github/license/Madmartigan1/tradebot)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.13%2B-blue)](requirements.txt)
![Code size](https://img.shields.io/github/languages/code-size/Madmartigan1/tradebot)
[![Last commit](https://img.shields.io/github/last-commit/Madmartigan1/tradebot)](https://github.com/Madmartigan1/tradebot/commits/main)
[![Open issues](https://img.shields.io/github/issues/Madmartigan1/tradebot)](https://github.com/Madmartigan1/tradebot/issues)
[![Open PRs](https://img.shields.io/github/issues-pr/Madmartigan1/tradebot)](https://github.com/Madmartigan1/tradebot/pulls)
[![Stars](https://img.shields.io/github/stars/Madmartigan1/tradebot?style=social)](https://github.com/Madmartigan1/tradebot/stargazers)

⚙️ **Improved autotune BLEND functionality. Added CLI overrides.**

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
  - **Take-Profit (8%+)**: When profits reach a safe margin, the Quartermaster locks the cargo and sends the ship home — a quick market exit.  

- **Deckhand (36h, ±2%)**: The “broom” that sweeps idle trades off the deck when they drift aimlessly without momentum, keeping the decks lean and ready for action.
  This ensures capital is recycled efficiently while the Captain (EMA) and Advisors (RSI/MACD) focus on live opportunities.

- **Swab → Deck Maintenance & Logkeeper** The newest crew member in v1.0.8 — responsible for keeping the decks spotless and logs consistent. Handles processed fill pruning and record hygiene to prevent bloat.
(*Fun fact:* The term “Swab” was inspired by *Captain Ron* — because every good ship needs a swab.)

- **Watchdog → Connection Officer**  
  The newest recruit in v1.1.1 — a loyal sentry who keeps the fleet online and alert.  
  - **Duties:** Monitors the WebSocket line for silence, issues pings, reconnects when idle, and even switches to local candle tracking if the exchange link grows unstable.    
  The Watchdog ensures the Captain (EMA) never sails blind, keeping communication alive through calm and storm alike.


Together they form a chain of command:
**EMA (Captain)** gives orders -> **AutoTune (Navigator)** continuously adjusts the fleet’s heading based on market weather -> **MACD (Commodore)** ensures strategy aligns with the broader trend -> **RSI (Skipper)** vetoes reckless moves -> **Quartermaster** secures profits and clears stagnation -> **Deckhand** keeps the decks clear of idle trades, ensuring the fleet stays agile and battle-ready -> **Swab** ensures no duplicate fills, stale positions, or misaligned logs remain aboard -> **Watchdog** keeps watch over the horizon, ensuring communication lines stay open and the ship never drifts alone. 

---

## 📖 Documentation
- **Full User Guide (PDF):** [docs/OpsManual.pdf](docs/OpsManual.pdf)
- More docs:
  - [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
  - [docs/RUNBOOK.md](docs/RUNBOOK.md)
  - [USAGE.md](USAGE.md)
  - [CONTRIBUTING.md](CONTRIBUTING.md)
  - [CHANGELOG.md](CHANGELOG.md)

---

## ✨ v1.1.2 Highlights
- **Command-line overrides**: you can now change core settings at runtime without editing files.
  Examples:
  ```bash
  python main.py --dry-run=true
  python main.py --enable-quartermaster=false
  python main.py --usd-per-order=30 --max-spend-cap=300
  ```
- **Quartermaster 2.0**  
  The Take-Profit & Stagnation Officer now includes:
  - Live-balance verification before exits  
  - Dust suppression window (30 min default)  
  - Internal throttles to prevent duplicate signals within a candle  
  - Market-only execution with accurate `exit_reason` tagging in `trades.csv`.
  
- **Smarter BLEND regime weighting**  
  AutoTune now applies quantized, weighted, and bounded adjustments instead of fractional drifts — leading to more stable transitions between choppy and trending modes.

- **Local Candle Settle Queue**  
  Local-mode aggregation now delays candle closes by 150 ms to ensure the last ticks of each bucket are captured, eliminating premature crossovers.

- **Improved portfolio realism**  
  On startup, live exchange balances are cross-checked; if the cache shows zero but funds exist, the bot seeds positions automatically.

- **Enhanced stability**  
  Quartermaster, spend tracking, and CSV logging now operate with unified state locks.  
  Logs include the friendly “YO SWAB!” hygiene line whenever fills are pruned.
  
- **Smarter BLEND regime weighting**  
  AutoTune now applies quantized, weighted, and bounded adjustments instead of fractional drifts — leading to more stable transitions between choppy and trending modes.

---

### Upgrade notes
- Backward-compatible with v1.0.9 state files.  
- No CSV header changes.  
- Daily-spend logic tightened to count only successfully accepted orders.

---

## Risk controls

- **Daily BUY cap**: limits the number of BUYs per day to curb overtrading during chop. Logged as  
  `**********Daily BUY cap reached (N). Skipping further BUYs.**********`
- **Quartermaster**: take-profit and stagnation exits; respects `base_increment` and `min_market_base_size`.
- **Exits**: `MARKET_ONLY` by default for deterministic fills under stress.

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
