"""src/shadow/evaluate_delayed_live_shadow.py
Phase R4: Delayed Evaluator for Live Shadow Predictions.

Operational Rule:
- Enforces 24-hour maturation window: Any prediction where (now - prediction_time) < 24 hours
  is marked UNSCORED and excluded from performance metrics until ground truth has matured.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

import pandas as pd

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("src.shadow.evaluate_delayed_live_shadow")

ROOT = Path(__file__).resolve().parent.parent.parent
LIVE_LOG_JSONL = ROOT / "data/processed/live_shadow_predictions.jsonl"
REPORTS_DIR = ROOT / "reports"


def evaluate_delayed_predictions() -> Dict[str, Any]:
    if not LIVE_LOG_JSONL.exists():
        logger.warning(f"Live log {LIVE_LOG_JSONL} does not exist yet.")
        return {"status": "NO_LOG_FILE"}

    records = []
    with open(LIVE_LOG_JSONL, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                records.append(json.loads(line))

    if not records:
        return {"status": "EMPTY_LOG"}

    df = pd.DataFrame(records)
    now_utc = datetime.now(timezone.utc)
    df["dt"] = pd.to_datetime(df["timestamp"], utc=True)

    # 24-hour maturation filter
    mature_mask = (now_utc - df["dt"]).dt.total_seconds() >= 24 * 3600.0
    mature_df = df[mature_mask]
    unscored_df = df[~mature_mask]

    logger.info(f"Total live predictions: {len(df):,}")
    logger.info(f"Matured (> 24h old): {len(mature_df):,} | Pending maturation (< 24h old): {len(unscored_df):,}")

    report = {
        "evaluation_time_utc": now_utc.isoformat(),
        "total_live_predictions": len(df),
        "matured_predictions_count": len(mature_df),
        "unscored_pending_count": len(unscored_df),
        "maturation_guard": "Predictions under 24 hours old are strictly excluded from accuracy scoring.",
    }

    out_file = REPORTS_DIR / "phase_r4_live_delayed_evaluation.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    return report


if __name__ == "__main__":
    evaluate_delayed_predictions()
