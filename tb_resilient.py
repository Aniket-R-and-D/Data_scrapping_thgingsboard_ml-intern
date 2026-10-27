"""tb_resilient.py
Resilient ThingsBoard API Client & HTTP Transport.
Canonical implementation lives in `src.ingest.tb_resilient`.
"""
from __future__ import annotations
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.ingest.tb_resilient import *
