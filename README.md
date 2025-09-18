# Tradebot v1.0.1

🚀 **Candle-based trading by default**  
This release marks the transition from tick-driven signals (v1.0.0 and earlier) to **candle closes** (default: 5-minute). Ticks can still be used via local aggregation or config tweaks, but candles are recommended for smoother, less noisy signals.

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

## 📄 Documentation
- **Full User Guide (PDF):** [docs/README.pdf](docs/README.pdf)  
- Additional docs:
  - [USAGE.md](USAGE.md)
  - [CONTRIBUTING.md](CONTRIBUTING.md)
  - [CHANGELOG.md](CHANGELOG.md)

---

## ✨ Highlights in v1.0.1
- **Candle-based mode (default 5m)** with backfill/warmup and `confirm_candles=3`.
- **KPI CSV logging** to `.state/trades.csv` (slippage, fees, liquidity, hold time).
- **Advisors refactor:** MACD normalized to bps; RSI veto simplified to block only unsafe sides.
- **Risk defaults tightened:**  
  - `dry_run=False`  
  - `$20` per order  
  - `$120` daily cap  
  - 15-minute cooldown  
  - Hard stop at -120 bps
- **Products updated:** new assets added (FIL-USD, DOT-USD, ARB-USD).

---

## 🔐 Secrets
Copy `APIkeys.env.example` → `APIkeys.env` and fill in your Coinbase Advanced API keys.  
`APIkeys.env` is **.gitignored** — never commit real keys.

---

## 🛠️ Quickstart
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy APIkeys.env.example APIkeys.env   # fill your keys
python .\main.py
