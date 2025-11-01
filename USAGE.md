# USAGE

## 0) First-time setup (do this once)
```bash
python -m venv .venv

# Activate the venv (you'll do this every session; see next section)
# Windows (PowerShell): .\.venv\Scripts\Activate.ps1
# Windows (CMD)      : .\.venv\Scripts\activate.bat
# macOS/Linux        : source .venv/bin/activate

python -m pip install --upgrade pip
pip install -r requirements.txt

cp APIkeys.env.example APIkeys.env
# Edit APIkeys.env and set:
# COINBASE_API_KEY=YOUR_COINBASE_API_KEY
# COINBASE_API_SECRET=YOUR_COINBASE_API_SECRET
# COINBASE_PORTFOLIO_ID=YOUR_PORTFOLIO_UUID   # optional; omit to use default portfolio
```

## 1) Every new terminal session (after reboot/sleep or a new window)
```bash
# Reactivate the existing venv
# Windows (PowerShell): .\.venv\Scripts\Activate.ps1
# Windows (CMD)       : .\.venv\Scripts\activate.bat
# macOS/Linux         : source .venv/bin/activate
```

## 2) Run the bot
```bash
python main.py
```

### CLI flags (optional overrides)
You can override selected `config.py` values from the command line for a single run:

```bash
# Paper trade without live orders
python main.py --dry-run=true

# Disable Quartermaster exits
python main.py --enable-quartermaster=false

# Adjust per-order size and daily cap
python main.py --usd-per-order=30 --max-spend-cap=300

# Choose coins to trade (see help page for combinations)
python main.py --coins=BTC-USD,ALGO-USD,PEPE-USD,AVAX-USD

# See the help page for detailed info
python main.py --help
```


<!-- latest version -->
