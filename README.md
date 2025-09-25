# Tradebot v1.0

🚀 **First stable release (no longer beta)**

Tradebot is an automated crypto trading bot for **Coinbase Advanced**.  
It uses an **EMA crossover** strategy with **RSI/MACD advisors**, plus risk controls like daily caps, cooldowns, and optional stop-loss tolerance.

---

## 📄 Documentation
- **Full User Guide (PDF):** [docs/README.pdf](docs/README.pdf)
- More docs:
  - [USAGE.md](USAGE.md)
  - [CONTRIBUTING.md](CONTRIBUTING.md)
  - [CHANGELOG.md](CHANGELOG.md)

---

## ✨ v1.0 Highlights
- Out of beta — **first stable release**
- EMA crossover with **dead-band** to reduce flapping
- RSI & MACD **advisors** to veto risky entries/exits
- **Maker-prefer** orders with per-asset bps offsets
- **Risk controls:** daily spend cap, cooldowns, stop-loss tolerance
- Session P&L baselines and runtime footer logging

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
## ?? Disclaimer:
This bot is intended for educational and experimental purposes only. It is not financial advice and will not guarantee profit. Use it at your own risk.
Always do your own research, monitor your trades, and configure the system to match your risk tolerance.
Past performance is not indicative of future results. Trade responsibly.
