"""nightly_risk_chain.py
Nightly Risk Chain Orchestrator.
Canonical implementation lives in `src.rules.nightly_risk_chain`.
"""
from __future__ import annotations
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.rules.nightly_risk_chain import *

if __name__ == "__main__":
    from src.rules.nightly_risk_chain import main
    main()
