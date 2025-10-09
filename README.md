# Tradebot v1.0.8

[![Latest release](https://img.shields.io/github/v/release/Madmartigan1/tradebot?sort=semver)](https://github.com/Madmartigan1/tradebot/releases)
[![License](https://img.shields.io/github/license/Madmartigan1/tradebot)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.13%2B-blue)](requirements.txt)
![Code size](https://img.shields.io/github/languages/code-size/Madmartigan1/tradebot)
[![Last commit](https://img.shields.io/github/last-commit/Madmartigan1/tradebot)](https://github.com/Madmartigan1/tradebot/commits/main)
[![Open issues](https://img.shields.io/github/issues/Madmartigan1/tradebot)](https://github.com/Madmartigan1/tradebot/issues)
[![Open PRs](https://img.shields.io/github/issues-pr/Madmartigan1/tradebot)](https://github.com/Madmartigan1/tradebot/pulls)
[![Stars](https://img.shields.io/github/stars/Madmartigan1/tradebot?style=social)](https://github.com/Madmartigan1/tradebot/stargazers)

⚙️ **Maintenance and resilience upgrades — a cleaner deck, steadier sails, and sharper command discipline**

Tradebot v1.0.8 focuses on **robustness, data consistency, and operational hygiene**.  
It tightens internal bookkeeping, prevents duplicate reconciliations, and adds self-checks to ensure the fleet remains seaworthy through long voyages.
Also introducing a smarter BLEND mechanism.
Instead of small, barely visible float adjustments, the tuner now applies quantized, weighted, and bounded moves toward the prevailing market regime.

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

- **Deckhand (48 h, ±2%)**: The “broom” that sweeps idle trades off the deck when they drift aimlessly without momentum, keeping the decks lean and ready for action.
  This ensures capital is recycled efficiently while the Captain (EMA) and Advisors (RSI/MACD) focus on live opportunities.

- **Swab → Deck Maintenance & Logkeeper** The newest crew member in v1.0.8 — responsible for keeping the decks spotless and logs consistent. Handles processed fill pruning and record hygiene to prevent bloat.
(*Fun fact:* The term “Swab” was inspired by *Captain Ron* — because every good ship needs a swab.)

Together they form a chain of command:
**EMA (Captain)** gives orders -> **AutoTune (Navigator)** continuously adjusts the fleet’s heading based on market weather -> **MACD (Commodore)** ensures strategy aligns with the broader trend -> **RSI (Skipper)** vetoes reckless moves -> **Quartermaster** secures profits and clears stagnation -> **Deckhand** keeps the decks clear of idle trades, ensuring the fleet stays agile and battle-ready -> **Swab** ensures no duplicate fills, stale positions, or misaligned logs remain aboard.

---

## 📖 Documentation
- **Full User Guide (PDF):** [docs/OpsManual.pdf](docs/OpsManual.pdf)
- More docs:
  - [USAGE.md](USAGE.md)
  - [CONTRIBUTING.md](CONTRIBUTING.md)
  - [CHANGELOG.md](CHANGELOG.md)

---

## ✨ v1.0.8 Highlights

- **Persistent `entry_time` tracking:**  
  Each position’s open timestamp now survives restarts, improving hold-time analytics.
- **Full-exit “shave” logic:**  
  Prevents dust mismatches and `INSUFFICIENT_FUND` preview errors when closing full positions.
- **Live-balance sanity check:**  
  Verifies available base balance before any SELL to stop phantom exits.
- **Processed-fills helper integration:**  
  Automatic pruning and safer persistence to avoid reprocessing past trades.
- **Header consistency check:**  
  A one-time startup audit warns if `trades.csv` ever drifts from the expected schema.
- **Internal cleanup:**  
  Duplicate imports removed, safer exception handling, and improved logging clarity for SELL responses.

---

### Upgrade notes

- This version is **state-compatible** with v1.0.7.  
  You can keep your `.state/portfolio.json` and `.state/trades.csv` files intact.
- Old phantom positions are automatically reconciled and cleaned.
- No CSV header or format changes — historical logs remain valid.
- Adjusted autotune function. Each vote adjusts knobs by up to 2 bps, rounded to 0.5 bps, with per-knob learning rates for smooth adaptation.
- The golden choppy preset remains the bot’s stable baseline.

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

