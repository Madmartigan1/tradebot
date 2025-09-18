Tradebot v1.0.2

🚀 Candle-based trading by default
This release continues with candle closes (default: 5-minute, confirm_candles=3). Tick emulation is still possible via local aggregation, but candles remain recommended for smoother, less noisy signals.


⚓ The Fleet Metaphor

To make the strategy easier to visualize:

EMA → Captain
The EMA crossover is the leader and core signal engine. When the short EMA crosses the long EMA, the Captain gives the order.

MACD → Commodore
The Commodore confirms momentum and trend direction. If the histogram disagrees, the Captain’s signal can be overridden.

RSI → Skipper
The Skipper keeps things safe in the short term. If RSI shows overbought/oversold, trades are vetoed even if the Captain/Commodore want action.

Together, they form a chain of command: EMA (Captain) gives orders, MACD (Commodore) ensures strategy aligns with trend, and RSI (Skipper) vetoes reckless moves.


📄 Documentation

Full User Guide (PDF): docs/README.pdf

Additional docs:

USAGE.md

CONTRIBUTING.md

CHANGELOG.md


✨ Highlights in v1.0.2

Refined maker-limit logic: order pricing now strictly aligned to Coinbase increments.

Repricing controls: new options for unfilled maker orders (reprice_each_candle, max_reprices_per_signal, etc.).

KPI CSV logging expanded: now includes slippage (abs & bps) and hold time per trade.

Risk & advisors tweaked: daily cap raised to $160; RSI defaults relaxed to 60/40; MACD thresholds set at ±3 bps.

EMA deadband: 8 bps neutral zone reduces whipsaw crossovers.

State handling improved: customizable state dir (BOT_STATE_DIR), safer persistence, log rotation for trade logs.


🔐 Secrets

Copy APIkeys.env.example → APIkeys.env and fill in your Coinbase Advanced API keys.
APIkeys.env is .gitignored — never commit real keys.


🛠️ QuickStart

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy APIkeys.env.example APIkeys.env   # fill your keys
python .\main.py
