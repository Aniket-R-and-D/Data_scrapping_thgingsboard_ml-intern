"""alarms_bridge.py
ThingsBoard Alarm Severity & Event Bridge.
Canonical implementation lives in `src.rules.alarms_bridge`.
"""
from __future__ import annotations
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.rules.alarms_bridge import *
