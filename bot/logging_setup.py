# bot/logging_setup.py
import logging
import sys
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

try:
    from .constants import STATE_DIR
except Exception:
    STATE_DIR = Path(".state")

def setup_logging(
    level: int = logging.INFO,
    to_file: bool = False,
    filename: str = "bot.log",
    max_mb: int = 10,
    backups: int = 3,
    use_utc: bool = False,
) -> logging.Logger:
    root = logging.getLogger()
    root.setLevel(level)

    for h in list(root.handlers):
        root.removeHandler(h)

    fmt = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    formatter = logging.Formatter(fmt)
    if use_utc:
        formatter.converter = time.gmtime

    sh = logging.StreamHandler(sys.stdout)
    sh.setLevel(level)
    sh.setFormatter(formatter)
    root.addHandler(sh)

    if to_file:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        log_path = STATE_DIR / filename
        fh = RotatingFileHandler(
            log_path, maxBytes=max_mb * 1024 * 1024, backupCount=backups, encoding="utf-8"
        )
        fh.setLevel(level)
        fh.setFormatter(formatter)
        root.addHandler(fh)

    for name in ("urllib3", "websockets", "coinbase", "asyncio"):
        logging.getLogger(name).setLevel(logging.WARNING)

    return root
