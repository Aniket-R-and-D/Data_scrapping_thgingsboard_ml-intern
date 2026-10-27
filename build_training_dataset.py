"""build_training_dataset.py
Training Dataset Pipeline for 365-day LightGBM Outage Predictor.
Canonical implementation lives in `src.features.build_training_dataset`.
"""
from __future__ import annotations
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.features.build_training_dataset import *

if __name__ == "__main__":
    from src.features.build_training_dataset import main
    main()
