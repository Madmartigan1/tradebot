# Tradebot v1.1.7

[![Latest version](https://img.shields.io/github/v/release/Madmartigan1/tradebot?sort=semver&include_prereleases)](https://github.com/Madmartigan1/tradebot/releases)
[![License](https://img.shields.io/github/license/Madmartigan1/tradebot)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.13%2B-blue)](requirements.txt)
![Code size](https://img.shields.io/github/languages/code-size/Madmartigan1/tradebot)
[![Last commit](https://img.shields.io/github/last-commit/Madmartigan1/tradebot)](https://github.com/Madmartigan1/tradebot/commits/main)
[![Open issues](https://img.shields.io/github/issues/Madmartigan1/tradebot)](https://github.com/Madmartigan1/tradebot/issues)
[![Open PRs](https://img.shields.io/github/issues-pr/Madmartigan1/tradebot)](https://github.com/Madmartigan1/tradebot/pulls)
[![Stars](https://img.shields.io/github/stars/Madmartigan1/tradebot?style=social)](https://github.com/Madmartigan1/tradebot/stargazers)

---

⚙️ Full CLI Control, AutoTune Transparency and Runtime Flexibility

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
  - [CHANGELOG.md](CHANGELOG.md)

---

## ✨ v1.1.7 Highlights
- **RSI initialization corrected** — The RSI indicator now properly seeds itself using a
  simple average of the first `rsi_period` gains/losses before switching to Wilder's
  smoothing. Previously it exited the seed phase after a single delta, producing inaccurate
  RSI values throughout the warmup window and potentially causing the Skipper (RSI advisor)
  to veto valid signals on bad data.
- **AutoTune BLEND mode now moves non-BPS knobs meaningfully** — The per-vote delta cap
  is now knob-specific. `per_coin_cooldown_s` was previously capped at 2 seconds per
  AutoTune cycle (against a 300–1800s range), making the Navigator's cooldown adjustments
  effectively inert in BLEND mode. It is now capped at 60s per cycle, allowing convergence
  in 3–5 cycles.
- **Quartermaster dust suppression no longer silences the Captain** — When QM detects a
  dust position too small to sell, it no longer exits `_on_candle_close` entirely. The EMA
  captain now continues to evaluate signals normally; only a successfully placed QM sell
  triggers the early exit.

---

### 🔧 Upgrade notes
- Pure bug fixes — no new config keys, no CSV header changes.
- Fully backward-compatible with v1.1.6 state files.
- RSI warmup behavior changes only during the first `rsi_period` candles after startup or
  reset. Post-warmup RSI is unaffected.

---

### 🧑‍💻 Developer Notes
- `_MAX_DELTA_PER_VOTE_BPS` in `autotune.py` replaced with per-knob `_MAX_DELTA_PER_VOTE`
  dict. Any future knobs not explicitly listed fall back to `2.0`.
- QM block in `_on_candle_close` restructured from early-`return` guards to `if/elif/else`
  chain. Only the `else` branch (real QM sell placed) returns early.
  
---

## ⚖️ Risk controls

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
