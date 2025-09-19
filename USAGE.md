# USAGE

## 0) One-time setup
```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux:
# source .venv/bin/activate

pip install -r requirements.txt
cp APIkeys.env.example APIkeys.env
# Edit APIkeys.env and set:
#   COINBASE_API_KEY=...
#   COINBASE_API_SECRET=...
#   COINBASE_PORTFOLIO_ID=...   # optional

#Run the  program
>>>python main.py
