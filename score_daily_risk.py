"""score_daily_risk.py
Production Inference Engine for Fleet Risk Scoring.
Canonical implementation lives in `src.predict.score_daily_risk`.
"""
from __future__ import annotations
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.predict.score_daily_risk import *

if __name__ == "__main__":
    from src.predict.score_daily_risk import main
    main()
