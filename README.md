# Tradebot v1.0.5

[![Latest release](https://img.shields.io/github/v/release/Madmartigan1/tradebot?sort=semver)](https://github.com/Madmartigan1/tradebot/releases)
[![License](https://img.shields.io/github/license/Madmartigan1/tradebot)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.13%2B-blue)](requirements.txt)
[![Last commit](https://img.shields.io/github/last-commit/Madmartigan1/tradebot)](https://github.com/Madmartigan1/tradebot/commits/main)
[![Open issues](https://img.shields.io/github/issues/Madmartigan1/tradebot)](https://github.com/Madmartigan1/tradebot/issues)
[![Open PRs](https://img.shields.io/github/issues-pr/Madmartigan1/tradebot)](https://github.com/Madmartigan1/tradebot/pulls)
[![Stars](https://img.shields.io/github/stars/Madmartigan1/tradebot?style=social)](https://github.com/Madmartigan1/tradebot/stargazers)

🚀 **Refinements & smarter regime handling**

Tradebot is an automated crypto trading bot for **Coinbase Advanced**.  
It uses an **EMA crossover** strategy with **RSI/MACD advisors**, plus risk controls like daily caps, cooldowns, and optional stop-loss tolerance.  
By default, it now runs on **candle closes** (5-minute interval, `confirm_candles=3`).

---

## ⚓ The Fleet Metaphor
To make the strategy easier to visualize:

- **EMA → Captain**  
  The EMA crossover is the leader and core signal engine. When the short EMA crosses the long EMA, the Captain gives the order.

- **MACD → Commodore**  
  The Commodore confirms momentum and trend direction. If the histogram disagrees, the Captain’s signal can be overridden.

- **RSI → Skipper**  
  The Skipper keeps things safe in the short term. If RSI shows overbought/oversold, trades are vetoed even if the Captain/Commodore want action.

Together, they form a chain of command: **EMA (Captain)** gives orders, **MACD (Commodore)** ensures strategy aligns with trend, and **RSI (Skipper)** vetoes reckless moves.

---

## 📖 Documentation
- **Full User Guide (PDF):** [docs/README.pdf](docs/README.pdf)
- More docs:
  - [USAGE.md](USAGE.md)
  - [CONTRIBUTING.md](CONTRIBUTING.md)
  - [CHANGELOG.md](CHANGELOG.md)

---

## ✨ v1.0.5 Highlights
- **Hybrid AutoTune regimes (new):**
  - Snap to strict regime if ≥70% vote share.
  - Blend winner ↔ Choppy if 55–69% (only sensitivity knobs).
  - Force Choppy if <55%.
- **Blended clamps** keep values safe while interpolating.
- **Enhanced logging:**
  - Mode (SNAP / BLEND / CHOPPY), vote shares, blend alpha, and global changes printed clearly.
  - Advisory-only “would disable” products (telemetry only).
  - Per-product offsets visible after 3-day KPI nudges.
- **No changes to indicator periods** (RSI/MACD structure stays fixed).
- **Offsets and floors:** still adaptive ±1 bps per coin, majors protected with lower floors.

---

### Upgrade notes
- You’ll now see **BLEND** logs when the market is ambiguous (55–69% votes).  
  Example:  
  - Everything else (bot loop, safety rails, telemetry-only disables) remains unchanged.
  - Removed final reconcile to avoid redundancy. A fresh reconcile is done on each run.

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

