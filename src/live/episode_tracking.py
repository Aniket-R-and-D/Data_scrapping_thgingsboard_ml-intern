"""src/live/episode_tracking.py — Alert episode tracking and rate normalization.

Tracks connectivity and power alerts as contiguous episodes rather than raw
15-minute repeated alerts. Calculates episode lifecycles, overlaps, and
episode-level alerts per device per week without changing the underlying alerting policy.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

LIVE_DIR = ROOT / "data" / "live"
DEFAULT_PREDICTIONS_FILE = LIVE_DIR / "shadow_predictions.jsonl"
POWER_ALERT_TIERS = {"HIGH", "CRITICAL"}
CONNECTIVITY_TIER = "CONNECTIVITY_ISSUE"


@dataclass
class AlertEpisode:
    episode_id: str
    device_id: str
    episode_type: str  # "connectivity" or "power"
    start_time: str
    end_time: Optional[str]
    duration_hours: float
    raw_alert_count: int
    is_open: bool
    peak_tier: str


def load_prediction_df(predictions_file: Optional[Path] = None) -> pd.DataFrame:
    p = predictions_file or DEFAULT_PREDICTIONS_FILE
    if not p.exists():
        return pd.DataFrame()
    rows = []
    with open(p, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    # Parse timestamp
    time_col = "prediction_time" if "prediction_time" in df.columns else "cycle_id"
    df["dt"] = pd.to_datetime(df[time_col], utc=True)
    df = df.sort_values(["device_id", "dt"]).reset_index(drop=True)
    return df


def extract_episodes_from_df(
    df: pd.DataFrame,
    max_gap_hours: float = 2.0,
) -> Dict[str, List[AlertEpisode]]:
    """Extract connectivity and power episodes from a sorted prediction DataFrame.

    An episode is a contiguous sequence of alert cycles for a device.
    A gap of > max_gap_hours between alerts closes the episode.
    """
    episodes: Dict[str, List[AlertEpisode]] = {
        "connectivity": [],
        "power": [],
    }
    if df.empty:
        return episodes

    # Max gap allowed before considering an episode broken/closed
    max_gap = timedelta(hours=max_gap_hours)

    for did, group in df.groupby("device_id"):
        group = group.sort_values("dt").reset_index(drop=True)

        for ep_type in ("connectivity", "power"):
            in_episode = False
            ep_start: Optional[datetime] = None
            last_alert_time: Optional[datetime] = None
            raw_count = 0
            peak_tier = ""

            for _, row in group.iterrows():
                tier = str(row.get("risk_tier", ""))
                is_alert = (
                    (tier == CONNECTIVITY_TIER)
                    if ep_type == "connectivity"
                    else (tier in POWER_ALERT_TIERS)
                )
                curr_time = row["dt"]

                if is_alert:
                    if not in_episode:
                        # Onset of new episode
                        in_episode = True
                        ep_start = curr_time
                        last_alert_time = curr_time
                        raw_count = 1
                        peak_tier = tier
                    else:
                        # Check gap between consecutive alert points
                        if last_alert_time and (curr_time - last_alert_time) > max_gap:
                            # Close previous episode due to elapsed gap
                            dur = (last_alert_time - ep_start).total_seconds() / 3600.0  # type: ignore
                            ep_id = f"{ep_type[:4].upper()}-{did[:8]}-{ep_start.isoformat()}"  # type: ignore
                            episodes[ep_type].append(
                                AlertEpisode(
                                    episode_id=ep_id,
                                    device_id=did,
                                    episode_type=ep_type,
                                    start_time=ep_start.isoformat(),  # type: ignore
                                    end_time=last_alert_time.isoformat(),
                                    duration_hours=round(dur, 2),
                                    raw_alert_count=raw_count,
                                    is_open=False,
                                    peak_tier=peak_tier,
                                )
                            )
                            # Start new episode at curr_time
                            ep_start = curr_time
                            last_alert_time = curr_time
                            raw_count = 1
                            peak_tier = tier
                        else:
                            # Extend current episode
                            raw_count += 1
                            last_alert_time = curr_time
                            if tier == "CRITICAL" or peak_tier != "CRITICAL":
                                peak_tier = tier
                else:
                    if in_episode:
                        # Transition out of alerting state
                        dur = (last_alert_time - ep_start).total_seconds() / 3600.0  # type: ignore
                        ep_id = f"{ep_type[:4].upper()}-{did[:8]}-{ep_start.isoformat()}"  # type: ignore
                        episodes[ep_type].append(
                            AlertEpisode(
                                episode_id=ep_id,
                                device_id=did,
                                episode_type=ep_type,
                                start_time=ep_start.isoformat(),  # type: ignore
                                end_time=curr_time.isoformat(),
                                duration_hours=round(dur, 2),
                                raw_alert_count=raw_count,
                                is_open=False,
                                peak_tier=peak_tier,
                            )
                        )
                        in_episode = False
                        ep_start = None
                        last_alert_time = None
                        raw_count = 0
                        peak_tier = ""

            # Check if still in episode at the end of observed data
            if in_episode and ep_start and last_alert_time:
                dur = (last_alert_time - ep_start).total_seconds() / 3600.0
                ep_id = f"{ep_type[:4].upper()}-{did[:8]}-{ep_start.isoformat()}"
                episodes[ep_type].append(
                    AlertEpisode(
                        episode_id=ep_id,
                        device_id=did,
                        episode_type=ep_type,
                        start_time=ep_start.isoformat(),
                        end_time=None,  # Still open
                        duration_hours=round(dur, 2),
                        raw_alert_count=raw_count,
                        is_open=True,
                        peak_tier=peak_tier,
                    )
                )

    return episodes


def check_episode_overlap(
    conn_episodes: List[AlertEpisode],
    power_episodes: List[AlertEpisode],
) -> List[Dict[str, Any]]:
    """Find overlapping connectivity and power alert episodes for the same device."""
    overlaps = []
    # Index by device_id
    conn_by_dev: Dict[str, List[AlertEpisode]] = {}
    for c in conn_episodes:
        conn_by_dev.setdefault(c.device_id, []).append(c)

    now_iso = datetime.now(timezone.utc).isoformat()
    for p in power_episodes:
        p_start = pd.to_datetime(p.start_time, utc=True)
        p_end = pd.to_datetime(p.end_time or now_iso, utc=True)

        for c in conn_by_dev.get(p.device_id, []):
            c_start = pd.to_datetime(c.start_time, utc=True)
            c_end = pd.to_datetime(c.end_time or now_iso, utc=True)

            # Check interval intersection
            if max(c_start, p_start) <= min(c_end, p_end):
                overlaps.append({
                    "device_id": p.device_id,
                    "connectivity_episode_id": c.episode_id,
                    "power_episode_id": p.episode_id,
                    "connectivity_start": c.start_time,
                    "power_start": p.start_time,
                    "overlap_start": max(c_start, p_start).isoformat(),
                    "overlap_end": min(c_end, p_end).isoformat(),
                })
    return overlaps


def compute_daily_episode_summary(
    date_str: str,
    episodes: Dict[str, List[AlertEpisode]],
    total_fleet_devices: int = 158,
    days_in_period: float = 1.0,
    raw_counts: Optional[Dict[str, int]] = None,
) -> Dict[str, Any]:
    """Calculate daily episode metrics for a target date (YYYY-MM-DD)."""
    conn_eps = episodes.get("connectivity", [])
    pwr_eps = episodes.get("power", [])

    # New episodes starting on date_str
    new_conn = [e for e in conn_eps if e.start_time[:10] == date_str]
    new_pwr = [e for e in pwr_eps if e.start_time[:10] == date_str]

    # Closed episodes ending on date_str
    closed_conn = [e for e in conn_eps if e.end_time and e.end_time[:10] == date_str]
    closed_pwr = [e for e in pwr_eps if e.end_time and e.end_time[:10] == date_str]

    # Open episodes on date_str (started on or before date_str, and not closed before date_str)
    def _is_active_on_day(e: AlertEpisode) -> bool:
        start_d = e.start_time[:10]
        end_d = e.end_time[:10] if e.end_time else "9999-99-99"
        return start_d <= date_str <= end_d

    open_conn = [e for e in conn_eps if _is_active_on_day(e)]
    open_pwr = [e for e in pwr_eps if _is_active_on_day(e)]

    # Overlaps active on this day
    overlaps = check_episode_overlap(open_conn, open_pwr)

    # Rate calculations (alerts per device per week)
    device_weeks = max(0.001, (total_fleet_devices * days_in_period) / 7.0)
    conn_rate_episode = len(new_conn) / device_weeks
    pwr_rate_episode = len(new_pwr) / device_weeks
    total_rate_episode = (len(new_conn) + len(new_pwr)) / device_weeks

    raw_conn = (raw_counts or {}).get("connectivity", sum(e.raw_alert_count for e in new_conn))
    raw_pwr = (raw_counts or {}).get("power", sum(e.raw_alert_count for e in new_pwr))
    raw_rate = (raw_conn + raw_pwr) / device_weeks

    # Deduplication ratio
    dedup_ratio = 1.0 - (total_rate_episode / max(1e-6, raw_rate)) if raw_rate > 0 else 0.0

    return {
        "report_date": date_str,
        "total_fleet_devices": total_fleet_devices,
        "device_weeks": round(device_weeks, 3),
        "new_connectivity_episodes": len(new_conn),
        "open_connectivity_episodes": len(open_conn),
        "closed_connectivity_episodes": len(closed_conn),
        "new_power_alert_episodes": len(new_pwr),
        "open_power_alert_episodes": len(open_pwr),
        "closed_power_alert_episodes": len(closed_pwr),
        "overlap_between_connectivity_and_power_episodes": len(overlaps),
        "overlap_details": overlaps,
        "episode_alerts_per_device_per_week": {
            "connectivity": round(conn_rate_episode, 4),
            "power": round(pwr_rate_episode, 4),
            "combined": round(total_rate_episode, 4),
        },
        "raw_alerts_per_device_per_week": round(raw_rate, 4),
        "duplicate_reduction_factor": round(dedup_ratio, 4),
        "episodes_summary": {
            "total_new_episodes": len(new_conn) + len(new_pwr),
            "total_active_episodes": len(open_conn) + len(open_pwr),
        },
    }


def format_episode_markdown(summary: Dict[str, Any]) -> str:
    """Format episode metrics as markdown table and narrative."""
    rates = summary["episode_alerts_per_device_per_week"]
    lines = [
        f"# Alert Episode Tracking Report — {summary['report_date']}",
        "",
        f"**Fleet Size:** {summary['total_fleet_devices']} registered devices  ",
        f"**Fleet Exposure:** {summary['device_weeks']} device-weeks  ",
        "**Policy Status:** Standard production alerting policy intact (no suppression applied)  ",
        "",
        "## 1. Episode Lifecycle Counts",
        "",
        "| Episode Category | New (Started Today) | Open (Active Today) | Closed (Resolved Today) |",
        "| :--- | :--- | :--- | :--- |",
        f"| **Connectivity** (`CONNECTIVITY_ISSUE`) | {summary['new_connectivity_episodes']} | {summary['open_connectivity_episodes']} | {summary['closed_connectivity_episodes']} |",
        f"| **Power Risk** (`HIGH` / `CRITICAL`) | {summary['new_power_alert_episodes']} | {summary['open_power_alert_episodes']} | {summary['closed_power_alert_episodes']} |",
        "",
        f"**Connectivity & Power Episode Overlap:** {summary['overlap_between_connectivity_and_power_episodes']} concurrent incident(s)",
        "",
        "## 2. Operational Alert Rate (Normalized per Device-Week)",
        "",
        "| Metric | Episode-Level Rate | Raw 15-Min Rate | Deduplication Factor |",
        "| :--- | :--- | :--- | :--- |",
        f"| **Combined Alerts** | **{rates['combined']:.3f}** / dev-week | {summary['raw_alerts_per_device_per_week']:.1f} / dev-week | **{summary['duplicate_reduction_factor'] * 100:.1f}%** |",
        f"| **Connectivity Only** | {rates['connectivity']:.3f} / dev-week | — | — |",
        f"| **Power Outage Risk** | {rates['power']:.3f} / dev-week | — | — |",
        "",
        "> [!NOTE]",
        "> Episode-level counting converts continuous 15-minute re-alerting into discrete operational incidents. "
        "Under episode counting, an ongoing connectivity condition is tracked as 1 incident rather than 672 raw tickets per week.",
    ]
    return "\n".join(lines) + "\n"


def track_and_report_episodes(
    date_str: Optional[str] = None,
    predictions_file: Optional[Path] = None,
    output_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    df = load_prediction_df(predictions_file)
    episodes = extract_episodes_from_df(df)

    if not date_str:
        if not df.empty:
            date_str = df["dt"].max().strftime("%Y-%m-%d")
        else:
            date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Get raw counts for day
    raw_counts = {}
    if not df.empty:
        day_df = df[df["dt"].dt.strftime("%Y-%m-%d") == date_str]
        raw_counts["connectivity"] = int((day_df["risk_tier"] == CONNECTIVITY_TIER).sum())
        raw_counts["power"] = int(day_df["risk_tier"].isin(POWER_ALERT_TIERS).sum())

    summary = compute_daily_episode_summary(
        date_str=date_str,
        episodes=episodes,
        raw_counts=raw_counts,
    )

    out_dir = output_dir or (ROOT / "reports")
    out_dir.mkdir(parents=True, exist_ok=True)

    json_path = out_dir / f"alert_episodes_{date_str}.json"
    md_path = out_dir / f"alert_episodes_{date_str}.md"

    json_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    md_path.write_text(format_episode_markdown(summary), encoding="utf-8")

    return summary


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--date", help="date to summarize (YYYY-MM-DD)")
    ap.add_argument("--output-dir", type=Path, default=None)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    summary = track_and_report_episodes(date_str=args.date, output_dir=args.output_dir)
    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        print(f"Episode tracking summary for {summary['report_date']}:")
        print(f"  New connectivity episodes: {summary['new_connectivity_episodes']}, Open: {summary['open_connectivity_episodes']}")
        print(f"  New power episodes: {summary['new_power_alert_episodes']}, Open: {summary['open_power_alert_episodes']}")
        print(f"  Episode alert rate: {summary['episode_alerts_per_device_per_week']['combined']:.3f} alerts/dev/week")
        print(f"  Duplicate reduction: {summary['duplicate_reduction_factor'] * 100:.1f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
