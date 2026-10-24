"""tests/test_episode_tracking.py

Tests for alert episode extraction, lifecycle tracking, overlap detection,
and normalized alerts per device-week calculation.
"""

from __future__ import annotations

import pandas as pd
import pytest

from src.live.episode_tracking import (
    AlertEpisode,
    check_episode_overlap,
    compute_daily_episode_summary,
    extract_episodes_from_df,
    format_episode_markdown,
)


def test_extract_episodes_from_df_contiguous_and_gap():
    # Construct DataFrame with 3 contiguous 15-min alerts, a 3-hour gap, then 1 alert
    records = [
        # Episode 1 for dev_A
        {
            "device_id": "dev_A",
            "prediction_time": "2026-10-15T10:00:00+00:00",
            "risk_tier": "CONNECTIVITY_ISSUE",
        },
        {
            "device_id": "dev_A",
            "prediction_time": "2026-10-15T10:15:00+00:00",
            "risk_tier": "CONNECTIVITY_ISSUE",
        },
        {
            "device_id": "dev_A",
            "prediction_time": "2026-10-15T10:30:00+00:00",
            "risk_tier": "CONNECTIVITY_ISSUE",
        },
        # Gap > 2 hours -> Episode 2 for dev_A
        {
            "device_id": "dev_A",
            "prediction_time": "2026-10-15T13:30:00+00:00",
            "risk_tier": "CONNECTIVITY_ISSUE",
        },
        # Power alert for dev_B
        {
            "device_id": "dev_B",
            "prediction_time": "2026-10-15T10:00:00+00:00",
            "risk_tier": "CRITICAL",
        },
    ]

    df = pd.DataFrame(records)
    df["dt"] = pd.to_datetime(df["prediction_time"], utc=True)

    episodes = extract_episodes_from_df(df, max_gap_hours=2.0)

    conn_eps = episodes["connectivity"]
    pwr_eps = episodes["power"]

    assert len(conn_eps) == 2
    assert conn_eps[0].device_id == "dev_A"
    assert conn_eps[0].raw_alert_count == 3
    assert conn_eps[1].device_id == "dev_A"
    assert conn_eps[1].raw_alert_count == 1

    assert len(pwr_eps) == 1
    assert pwr_eps[0].device_id == "dev_B"
    assert pwr_eps[0].peak_tier == "CRITICAL"


def test_check_episode_overlap():
    conn_ep = AlertEpisode(
        episode_id="CONN-dev_A-01",
        device_id="dev_A",
        episode_type="connectivity",
        start_time="2026-10-15T10:00:00+00:00",
        end_time="2026-10-15T12:00:00+00:00",
        duration_hours=2.0,
        raw_alert_count=8,
        is_open=False,
        peak_tier="CONNECTIVITY_ISSUE",
    )
    pwr_ep = AlertEpisode(
        episode_id="POWR-dev_A-01",
        device_id="dev_A",
        episode_type="power",
        start_time="2026-10-15T11:00:00+00:00",
        end_time="2026-10-15T13:00:00+00:00",
        duration_hours=2.0,
        raw_alert_count=8,
        is_open=False,
        peak_tier="HIGH",
    )

    overlaps = check_episode_overlap([conn_ep], [pwr_ep])
    assert len(overlaps) == 1
    assert overlaps[0]["device_id"] == "dev_A"
    assert overlaps[0]["connectivity_episode_id"] == "CONN-dev_A-01"
    assert overlaps[0]["power_episode_id"] == "POWR-dev_A-01"


def test_compute_daily_episode_summary_and_rates():
    conn_ep = AlertEpisode(
        episode_id="CONN-dev_1-01",
        device_id="dev_1",
        episode_type="connectivity",
        start_time="2026-10-15T08:00:00+00:00",
        end_time="2026-10-15T10:00:00+00:00",
        duration_hours=2.0,
        raw_alert_count=8,
        is_open=False,
        peak_tier="CONNECTIVITY_ISSUE",
    )
    pwr_ep = AlertEpisode(
        episode_id="POWR-dev_2-01",
        device_id="dev_2",
        episode_type="power",
        start_time="2026-10-15T12:00:00+00:00",
        end_time=None,  # Still open
        duration_hours=1.0,
        raw_alert_count=4,
        is_open=True,
        peak_tier="CRITICAL",
    )

    summary = compute_daily_episode_summary(
        date_str="2026-10-15",
        episodes={"connectivity": [conn_ep], "power": [pwr_ep]},
        total_fleet_devices=158,
        days_in_period=1.0,
    )

    assert summary["report_date"] == "2026-10-15"
    assert summary["new_connectivity_episodes"] == 1
    assert summary["closed_connectivity_episodes"] == 1
    assert summary["new_power_alert_episodes"] == 1
    assert summary["open_power_alert_episodes"] == 1

    # Device-weeks for 158 devices over 1 day = 158 / 7 = 22.571
    expected_device_weeks = round(158.0 / 7.0, 3)
    assert summary["device_weeks"] == expected_device_weeks

    # Episode alert rates per device-week
    rates = summary["episode_alerts_per_device_per_week"]
    assert rates["connectivity"] > 0
    assert rates["power"] > 0
    assert rates["combined"] == round(rates["connectivity"] + rates["power"], 4)

    # Markdown formatter check
    md = format_episode_markdown(summary)
    assert "Alert Episode Tracking Report" in md
    assert "Episode Lifecycle Counts" in md
    assert "dev-week" in md
