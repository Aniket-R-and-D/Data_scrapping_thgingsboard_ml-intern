"""pull_all_data.py
Fleet Telemetry & Attribute Ingestion Pipeline.
Canonical implementation lives in `src.ingest.pull_all_data`.
"""
from __future__ import annotations
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.ingest.pull_all_data import *

if __name__ == "__main__":
    from src.ingest.pull_all_data import main
    raise SystemExit(main(sys.argv[1:]))
