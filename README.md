# Tradebot v1.0.2

🚀 **Refinements & stability improvements**

Tradebot is an automated crypto trading bot for **Coinbase Advanced**.  
It uses an **EMA crossover** strategy with **RSI/MACD advisors**, plus risk controls like daily caps, cooldowns, and optional stop-loss tolerance.  
By default, it now runs on **candle closes** (5-minute interval, `confirm_candles=3`).

---

## 📄 Documentation
- **Full User Guide (PDF):** [docs/README.pdf](docs/README.pdf)
- More docs:
  - [USAGE.md](USAGE.md)
  - [CONTRIBUTING.md](CONTRIBUTING.md)
  - [CHANGELOG.md](CHANGELOG.md)

---

## ✨ v1.0.2 Highlights
- Refined **maker-limit logic**: prices rounded consistently to Coinbase increments
- **Repricing controls** for unfilled maker orders (`reprice_each_candle`, `max_reprices_per_signal`, etc.)
- **KPI CSV logging expanded**: now includes slippage (abs & bps) and hold time
- **Risk & advisors tweaked**: daily BUY cap $160, RSI defaults 60/40, MACD ±3 bps
- **EMA deadband**: 8 bps neutral zone to reduce false crossovers
- **Persistence improvements**: custom `.state/` dir via `BOT_STATE_DIR`, log rotation for trade logs

---

## 🔐 Secrets
Copy `APIkeys.env.example` → `APIkeys.env` and fill your Coinbase credentials.  
`APIkeys.env` is **.gitignored** — never commit real keys.

---

## 🛠️ Quickstart
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy APIkeys.env.example APIkeys.env   # fill your keys
python .\main.py
