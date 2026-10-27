#!/usr/bin/env python
"""scripts/cloud/cloud_runner.py
Autonomous 24/7/365 Cloud Daemon for SEPLE NovaEdge AI Engine.

Designed for AWS EC2, AWS Lightsail, and Docker containers:
1. Hourly/Interval Recovery Loop: Polls ThingsBoard live telemetry every N minutes (default: 15m)
   to detect hardware fixes, auto-clear alarms, and post instant recovery notifications.
2. Daily Scheduled Digest: Runs full 130-branch fleet scoring daily at a specified UTC hour (default: 06:00 UTC),
   sending executive Slack and Email briefs and refreshing ThingsBoard dashboards.
3. Supports --once flag for single-shot execution (e.g., in AWS ECS / Lambda / Cron).
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any

from dotenv import load_dotenv

# Ensure root directory is in sys.path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import send_daily_risk_digest as digest_app
from src.live.recovery_gate import probe_device_telemetry_live
from src.live.alarm_clear_engine import get_active_ai_alarms, clear_ai_alarm, sync_recovery_attributes

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("cloud_runner")


def run_full_daily_cycle(env: Dict[str, str]) -> None:
    """Execute complete daily scoring, reporting, and multi-channel dispatch."""
    logger.info("=" * 65)
    logger.info("Executing Full Daily AI Fleet Operations Cycle...")
    logger.info("=" * 65)

    try:
        fleet_df, summary = digest_app.compute_fleet_risk(env)
        md_p, html_p, csv_p = digest_app.generate_reports(fleet_df, summary)
        
        # Sync to ThingsBoard (clears recovered, registers active, updates modernized KPI card)
        digest_app.sync_to_thingsboard(fleet_df, env, summary=summary)

        # Dispatch Slack
        digest_app.send_slack_notification(fleet_df, summary, env)

        # Dispatch Email
        digest_app.send_email_notification(html_p, csv_p, summary, env)

        logger.info("Full Daily AI Fleet Operations Cycle completed successfully.")
        
        # Publish CloudWatch heartbeat & operational metrics
        try:
            from src.cloud.cloudwatch_metrics import publish_heartbeat
            publish_heartbeat(
                cycle_type="daily_digest",
                success=True,
                restored_count=summary.get("total_recovered", 0),
                critical_count=summary.get("critical_risk_count", 0),
                high_risk_count=summary.get("high_risk_count", 0)
            )
        except Exception as cw_err:
            logger.warning(f"Failed to publish CloudWatch heartbeat: {cw_err}")

    except Exception as e:
        logger.error(f"Error during full daily cycle: {e}", exc_info=True)
        try:
            from src.cloud.cloudwatch_metrics import publish_heartbeat
            publish_heartbeat(cycle_type="daily_digest", success=False)
        except Exception:
            pass


def run_recovery_probe_cycle(env: Dict[str, str]) -> int:
    """Lightweight cycle: check known critical devices for hardware restoration and auto-clear."""
    host = env.get("TB_HOST", "https://seple.iot-private.cloud").rstrip("/")
    email = env.get("TB_EMAIL")
    password = env.get("TB_PASSWORD")

    if not email or not password:
        logger.warning("ThingsBoard credentials missing; skipping recovery probe cycle.")
        return 0

    try:
        import requests
        resp = requests.post(f"{host}/api/auth/login", json={"username": email, "password": password}, timeout=10)
        if resp.status_code != 200:
            logger.error(f"Failed to authenticate with ThingsBoard: {resp.status_code}")
            return 0
        token = resp.json().get("token")
    except Exception as e:
        logger.error(f"Connection error to ThingsBoard: {e}")
        return 0

    # Read latest risk digest to find candidate critical/high-risk devices
    latest_json = ROOT / "reports/latest_risk_digest.json"
    if not latest_json.exists():
        logger.info("No prior risk digest found; running full cycle first.")
        run_full_daily_cycle(env)
        return 0

    import json
    with open(latest_json, "r", encoding="utf-8") as f:
        data = json.load(f)

    candidates = data.get("top_critical", []) + data.get("actionable_branches", [])
    seen_ids = set()
    unique_candidates = []
    for c in candidates:
        cid = c.get("device_id")
        if cid and cid not in seen_ids and not c.get("is_recovered", False):
            seen_ids.add(cid)
            unique_candidates.append(c)

    recovered_count = 0
    for cand in unique_candidates:
        dev_id = cand["device_id"]
        dev_name = cand["device_name"]
        
        eval_res = probe_device_telemetry_live(dev_id, host, token)
        if eval_res.is_recovered:
            logger.info(f"⚡ INSTANT RECOVERY DETECTED: {dev_name} ({dev_id})")
            
            # Clear alarms on ThingsBoard
            active_alarms = get_active_ai_alarms(dev_id, host, token)
            for a in active_alarms:
                aid = a.get("id", {}).get("id")
                if aid:
                    clear_ai_alarm(aid, host, token)

            # Sync attributes
            sync_recovery_attributes(dev_id, eval_res, host, token)
            recovered_count += 1

            # Post quick notification to Slack if webhook exists
            webhook = env.get("SLACK_WEBHOOK", "").strip()
            if webhook and webhook.startswith("https://hooks.slack.com/services/"):
                slack_payload = {
                    "text": f"✅ *Instant Branch Recovery Detected:* `{dev_name}` is restored online!\n"
                            f"• ThingsBoard Alarms: Cleared automatically.\n"
                            f"• URL: {host}/devices/{dev_id}"
                }
                try:
                    requests.post(webhook, json=slack_payload, timeout=5)
                except Exception as ex:
                    logger.debug(f"Slack post error: {ex}")

    logger.info(f"Recovery probe cycle completed: probed {len(unique_candidates)} critical branches; {recovered_count} newly restored.")
    if recovered_count > 0:
        logger.info(f"Recovery cycle cleared and updated {recovered_count} devices. Refreshing summary...")
        # Refresh full state (which publishes daily_digest metrics)
        run_full_daily_cycle(env)
    else:
        try:
            from src.cloud.cloudwatch_metrics import publish_heartbeat
            publish_heartbeat(
                cycle_type="recovery_probe",
                success=True,
                restored_count=0
            )
        except Exception as cw_err:
            logger.warning(f"Failed to publish CloudWatch recovery probe heartbeat: {cw_err}")

    return recovered_count


def daemon_loop(env: Dict[str, str]) -> None:
    """Autonomous scheduling daemon."""
    poll_minutes = int(os.getenv("POLL_INTERVAL_MINUTES", "15"))
    digest_hour_utc = int(os.getenv("DIGEST_HOUR_UTC", "6")) # 06:00 UTC = 11:30 AM IST
    retrain_day_of_month = int(os.getenv("RETRAIN_DAY_OF_MONTH", "1")) # 1st of month
    retrain_hour_utc = int(os.getenv("RETRAIN_HOUR_UTC", "2")) # 02:00 UTC (off-peak)

    logger.info(f"Starting 24/7 NovaEdge Cloud Daemon.")
    logger.info(f"Recovery Poll Interval: Every {poll_minutes} minutes")
    logger.info(f"Scheduled Daily Digest: Daily at {digest_hour_utc:02d}:00 UTC")
    logger.info(f"Scheduled Monthly Retraining: Day {retrain_day_of_month} at {retrain_hour_utc:02d}:00 UTC")

    # Run once at startup to ensure baseline state
    logger.info("Executing initial startup cycle...")
    run_full_daily_cycle(env)

    last_digest_day = datetime.now(timezone.utc).day
    last_retrain_month = datetime.now(timezone.utc).month

    while True:
        try:
            time.sleep(poll_minutes * 60)
            now = datetime.now(timezone.utc)
            
            # 1. Recovery probe check
            logger.info(f"Running periodic recovery probe ({now.strftime('%H:%M:%S UTC')})...")
            run_recovery_probe_cycle(env)

            # 2. Daily morning digest check
            if now.hour == digest_hour_utc and now.day != last_digest_day:
                logger.info(f"Scheduled Daily Digest Triggered ({digest_hour_utc}:00 UTC)")
                run_full_daily_cycle(env)
                last_digest_day = now.day

            # 3. Monthly model retraining check (captures seasonal grid drift)
            if now.day == retrain_day_of_month and now.hour == retrain_hour_utc and now.month != last_retrain_month:
                logger.info(f"Scheduled Monthly AI Model Retraining Triggered (Day {retrain_day_of_month}, {retrain_hour_utc}:00 UTC)")
                try:
                    from src.predict.monthly_retrainer import run_monthly_retraining
                    run_monthly_retraining(env)
                except Exception as retrain_err:
                    logger.error(f"Error during monthly retraining: {retrain_err}", exc_info=True)
                last_retrain_month = now.month

        except KeyboardInterrupt:
            logger.info("Daemon received interrupt signal. Exiting gracefully.")
            break
        except Exception as e:
            logger.error(f"Unexpected error in daemon loop: {e}", exc_info=True)
            time.sleep(60)


def main():
    parser = argparse.ArgumentParser(description="NovaEdge AI Cloud Runner Daemon")
    parser.add_argument("--once", action="store_true", help="Execute single full daily cycle and exit (for cron/scheduled tasks)")
    parser.add_argument("--probe-only", action="store_true", help="Execute single recovery probe cycle and exit")
    parser.add_argument("--daemon", action="store_true", help="Run in continuous 24/7 background scheduler loop")
    parser.add_argument("--retrain-now", action="store_true", help="Execute monthly model retraining immediately")
    parser.add_argument("--force-promote", action="store_true", help="Force promotion during retraining")
    args = parser.parse_args()

    env = digest_app.load_environment()

    if args.retrain_now:
        from src.predict.monthly_retrainer import run_monthly_retraining
        run_monthly_retraining(env, force_promote=args.force_promote)
    elif args.once:
        run_full_daily_cycle(env)
    elif args.probe_only:
        run_recovery_probe_cycle(env)
    elif args.daemon:
        daemon_loop(env)
    else:
        # Default behavior: run once and show status
        run_full_daily_cycle(env)


if __name__ == "__main__":
    main()
