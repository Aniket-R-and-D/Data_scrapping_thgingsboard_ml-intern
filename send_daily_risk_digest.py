"""send_daily_risk_digest.py
SEPLE NovaEdge AI Fleet Operations - Daily Risk Digest & Dispatcher.
Canonical implementation lives in `src.predict.send_daily_risk_digest`.
"""
from __future__ import annotations
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.predict.send_daily_risk_digest import *

if __name__ == "__main__":
    from src.predict.send_daily_risk_digest import main
    main()
