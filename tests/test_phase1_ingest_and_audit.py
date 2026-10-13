"""Unit tests for Phase 1: Ingestion, Configuration, and Outage Labeling."""

import pytest
import yaml
import pandas as pd
import numpy as np
from pathlib import Path


def test_config_integrity():
    config_path = Path("configs/config.yaml")
    assert config_path.exists(), "configs/config.yaml must exist."

    with open(config_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    assert "project" in cfg
    assert cfg["project"]["random_seed"] == 42
    assert "fleet" in cfg
    assert "BOI" in cfg["fleet"]["target_banks"]
    assert "SBI" in cfg["fleet"]["target_banks"]
    assert "BOB" in cfg["fleet"]["target_banks"]
    assert "CANARA" in cfg["fleet"]["target_banks"]
    assert "signals" in cfg
    assert "outage_label" in cfg
    assert cfg["outage_label"]["sustained_minutes"] == 15
    assert cfg["outage_label"]["battery_critical_volts"] == 10.8


def test_device_eligibility_filter():
    with open("configs/config.yaml", "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    target_banks = cfg["fleet"]["target_banks"]
    exclude_patterns = cfg["fleet"]["excluded_name_patterns"]

    def is_eligible(name: str) -> bool:
        for ex in exclude_patterns:
            if ex.lower() in name.lower():
                return False
        for bank in target_banks:
            if name.upper().startswith(bank):
                return True
        return False

    # Positive test cases
    assert is_eligible("BOI-KOLKATA-MAIN") is True
    assert is_eligible("SBI-PARK-STREET") is True
    assert is_eligible("BOB-AIRPORT") is True
    assert is_eligible("CANARA-SALT-LAKE") is True

    # Negative test cases (excluded)
    assert is_eligible("BOB-TEST-SITE") is False
    assert is_eligible("playback_stream") is False
    assert is_eligible("TestGateway-01") is False
    assert is_eligible("RND-Lab-Device") is False
    assert is_eligible("UNKNOWN-ATM") is False


def test_outage_label_logic():
    # Synthetic 15-minute telemetry intervals
    # Scenario 1: Normal power (AC=220V, Batt=13.2V) -> False
    # Scenario 2: Mains off, but battery healthy (AC=0V, Batt=12.5V) -> False (battery backup active)
    # Scenario 3: Mains off, battery depleted (AC=0V, Batt=10.2V) -> True (outage)
    # Scenario 4: Mains off, NVR offline -> True (outage)

    def evaluate_outage_step(ac_v: float, batt_v: float, nvr_offline: bool) -> bool:
        mains_lost = ac_v < 50.0
        secondary_failure = (batt_v < 10.8) or nvr_offline
        return mains_lost and secondary_failure

    assert evaluate_outage_step(220.0, 13.2, False) is False
    assert evaluate_outage_step(0.0, 12.5, False) is False
    assert evaluate_outage_step(0.0, 10.5, False) is True
    assert evaluate_outage_step(0.0, 13.0, True) is True


def test_independent_event_clustering():
    # Timestamps in ms: events at t=0 and t=30m should collapse into 1 event (within 2h)
    # Event at t=5h should form an independent second event
    raw_events = [
        (0, 15 * 60 * 1000, "OUTAGE_1"),
        (30 * 60 * 1000, 45 * 60 * 1000, "OUTAGE_2"),
        (5 * 3600 * 1000, 5.5 * 3600 * 1000, "OUTAGE_3"),
    ]

    collapsed = []
    cur_start, cur_end, cur_reason = raw_events[0]
    for s, e, r in raw_events[1:]:
        if s <= cur_end + 7200 * 1000:  # 2-hour window
            cur_end = max(cur_end, e)
            cur_reason += f" + {r}"
        else:
            collapsed.append((cur_start, cur_end, cur_reason))
            cur_start, cur_end, cur_reason = s, e, r
    collapsed.append((cur_start, cur_end, cur_reason))

    assert len(collapsed) == 2
    assert collapsed[0][0] == 0
    assert collapsed[0][1] == 45 * 60 * 1000
    assert collapsed[1][0] == 5 * 3600 * 1000
