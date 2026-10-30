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
from src.live.alarm_clear_engine import (
    build_recovery_dispatch_payload,
    clear_confirmed_recovery,
    load_debounce_state,
    pending_recovery_devices,
    save_debounce_state,
    summarize_mttr_by_bank,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("cloud_runner")


def run_full_daily_cycle(env: Dict[str, str], send_alerts: bool = True) -> None:
    """Execute complete daily scoring, reporting, and multi-channel dispatch."""
    logger.info("=" * 65)
    logger.info(f"Executing Full Daily AI Fleet Operations Cycle (send_alerts={send_alerts})...")
    logger.info("=" * 65)

    try:
        fleet_df, summary = digest_app.compute_fleet_risk(env)
        md_p, html_p, csv_p = digest_app.generate_reports(fleet_df, summary)
        
        # Sync to ThingsBoard (clears recovered, registers active, updates modernized KPI card)
        digest_app.sync_to_thingsboard(fleet_df, env, summary=summary)

        # Dispatch Slack (only when send_alerts is True)
        if send_alerts:
            digest_app.send_slack_notification(fleet_df, summary, env)
        else:
            logger.info("Alert dispatch disabled for this cycle; skipping Slack notification.")

        # Dispatch Email (only when send_alerts is True)
        if send_alerts:
            digest_app.send_email_notification(html_p, csv_p, summary, env)
        else:
            logger.info("Alert dispatch disabled for this cycle; skipping Email notification.")

        # Phase 7: telemetry drift monitoring + debounced recovery quality metrics
        quality = run_quality_monitoring(env)

        logger.info("Full Daily AI Fleet Operations Cycle completed successfully.")
        
        # Publish CloudWatch heartbeat & operational metrics
        try:
            from src.cloud.cloudwatch_metrics import publish_heartbeat, publish_quality_metrics
            publish_heartbeat(
                cycle_type="daily_digest",
                success=True,
                restored_count=summary.get("total_recovered", 0),
                critical_count=summary.get("critical_risk_count", 0),
                high_risk_count=summary.get("high_risk_count", 0)
            )
            if quality.get("drift_report"):
                publish_quality_metrics(
                    drift_report=quality["drift_report"],
                    recovery_state=quality.get("recovery_state"),
                    confirmed_recoveries=int(summary.get("serviced_recovered_count", 0) or 0),
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


def run_quality_monitoring(env: Dict[str, str]) -> Dict[str, Any]:
    """Evaluate telemetry drift, refresh quality metrics, and archive quality reports.

    Every step degrades gracefully: a drift failure must not abort the daily
    operational digest.
    """
    result: Dict[str, Any] = {"drift_report": None, "recovery_state": None, "archived": None}

    try:
        from src.features.drift_monitor import evaluate_drift
        result["drift_report"] = evaluate_drift()
        logger.info(
            f"Telemetry drift: overall={result['drift_report'].get('overall_status')}, "
            f"max_psi={result['drift_report'].get('max_psi')}"
        )
    except Exception as drift_err:
        logger.warning(f"Telemetry drift evaluation failed: {drift_err}")

    try:
        result["recovery_state"] = load_debounce_state()
    except Exception as state_err:
        logger.warning(f"Could not load recovery debounce state: {state_err}")

    try:
        from src.cloud.s3_archiver import archive_reports_to_s3
        result["archived"] = archive_reports_to_s3(ROOT / "reports")
    except Exception as s3_err:
        logger.warning(f"S3 quality archival failed: {s3_err}")

    return result


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
    debounce_file = ROOT / "reports/recovery_debounce_state.json"
    s3_bucket = env.get("S3_BUCKET", "bank-report-data")
    s3_prefix = env.get("S3_PREFIX", "novaedge-ai-reports").strip("/")

    # Attempt to restore state from S3 if running in stateless container (e.g. AWS ECS Fargate)
    try:
        import boto3
        s3 = boto3.client("s3", region_name=env.get("AWS_REGION", "ap-south-1"))
        if not latest_json.exists():
            latest_json.parent.mkdir(parents=True, exist_ok=True)
            s3.download_file(s3_bucket, f"{s3_prefix}/latest_risk_digest.json", str(latest_json))
            logger.info("Successfully restored latest_risk_digest.json from S3.")
        if not debounce_file.exists():
            debounce_file.parent.mkdir(parents=True, exist_ok=True)
            s3.download_file(s3_bucket, f"{s3_prefix}/recovery_debounce_state.json", str(debounce_file))
            logger.info("Successfully restored recovery_debounce_state.json from S3.")
    except Exception as s3_fetch_err:
        logger.debug(f"S3 state fetch skipped/failed: {s3_fetch_err}")

    if not latest_json.exists():
        logger.info("No prior risk digest found; computing fleet risk silently without email/slack alerts.")
        run_full_daily_cycle(env, send_alerts=False)
        if not latest_json.exists():
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

    # Devices already inside a debounce window must keep being probed even if a
    # fresh digest no longer lists them as critical.
    debounce_state = load_debounce_state()
    for pending in pending_recovery_devices(debounce_state):
        pid = pending.get("device_id")
        if pid and pid not in seen_ids:
            seen_ids.add(pid)
            unique_candidates.append(
                {
                    "device_id": pid,
                    "device_name": pending.get("device_name", pid),
                    "bank": pending.get("bank", "UNKNOWN"),
                }
            )

    recovered_count = 0
    recovered_devices = []
    for cand in unique_candidates:
        dev_id = cand["device_id"]
        dev_name = cand.get("device_name") or dev_id

        eval_res = probe_device_telemetry_live(dev_id, host, token)
        decision = clear_confirmed_recovery(
            device_id=dev_id,
            eval_res=eval_res,
            host=host,
            token=token,
            device_name=dev_name,
            bank=str(cand.get("bank") or "UNKNOWN"),
            state=debounce_state,
        )

        if decision.get("recovery_confirmed"):
            recovered_count += 1
            recovered_devices.append(decision)
            logger.info(f"⚡ DEBOUNCE-CONFIRMED RECOVERY: {dev_name} ({dev_id})")
        elif decision.get("eligible_for_clear") is False and decision.get("consecutive_nominal_cycles"):
            logger.info(
                f"⏳ Debounce pending for {dev_name} ({dev_id}): "
                f"{decision.get('consecutive_nominal_cycles')}/{decision.get('required_cycles')} "
                f"nominal cycles — {decision.get('reason')}"
            )

    try:
        saved_path = save_debounce_state(debounce_state)
        # Archive debounce state to S3 for serverless continuity
        try:
            import boto3
            s3 = boto3.client("s3", region_name=env.get("AWS_REGION", "ap-south-1"))
            s3.upload_file(str(saved_path), s3_bucket, f"{s3_prefix}/recovery_debounce_state.json")
        except Exception as s3_up_err:
            logger.debug(f"Could not upload debounce state to S3: {s3_up_err}")
    except Exception as state_err:
        logger.warning(f"Could not persist recovery debounce state: {state_err}")

    if recovered_devices:
        dispatch = build_recovery_dispatch_payload(recovered_devices, state=debounce_state)
        logger.info(
            f"Recovery dispatch: {dispatch['confirmed_count']} branch(es), "
            f"mean MTTR {dispatch.get('mean_mttr_minutes')} min."
        )
        _dispatch_recovery_notifications(env, dispatch)

    logger.info(
        f"Recovery probe cycle completed: probed {len(unique_candidates)} branches; "
        f"{recovered_count} confirmed recovered; "
        f"{len(pending_recovery_devices(debounce_state))} still inside a debounce window."
    )

    try:
        from src.cloud.cloudwatch_metrics import publish_heartbeat, publish_quality_metrics
        publish_heartbeat(
            cycle_type="recovery_probe",
            success=True,
            restored_count=recovered_count,
        )
        publish_quality_metrics(
            recovery_state=debounce_state,
            confirmed_recoveries=recovered_count,
        )
    except Exception as cw_err:
        logger.warning(f"Failed to publish CloudWatch recovery probe metrics: {cw_err}")

    if recovered_count > 0:
        logger.info("Refreshing fleet summary after confirmed recoveries...")
        run_full_daily_cycle(env)

    return recovered_count


def _dispatch_recovery_notifications(env: Dict[str, str], dispatch: Dict[str, Any]) -> None:
    """Send the debounced-recovery Slack block and email body."""
    import requests

    webhook = env.get("SLACK_WEBHOOK", "").strip()
    if webhook and webhook.startswith("https://hooks.slack.com/services/"):
        try:
            requests.post(webhook, json=dispatch["slack"], timeout=5)
        except Exception as ex:
            logger.debug(f"Recovery Slack post error: {ex}")

    try:
        _send_recovery_email(env, dispatch["email"]["subject"], dispatch["email"]["html"])
    except Exception as ex:
        logger.debug(f"Recovery email dispatch error: {ex}")


def _send_recovery_email(env: Dict[str, str], subject: str, html_body: str) -> None:
    """Send the recovery email using the digest's SMTP configuration."""
    import smtplib
    import ssl
    from email.message import EmailMessage

    smtp_email = env.get("SMTP_EMAIL", "").strip()
    smtp_password = env.get("SMTP_PASSWORD", "").strip()
    recipient = (env.get("EMAIL_TO") or "").strip()
    if not (smtp_email and smtp_password and recipient):
        logger.debug("SMTP configuration incomplete; skipping recovery email dispatch.")
        return

    smtp_host = env.get("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(env.get("SMTP_PORT", "587"))

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = smtp_email
    message["To"] = recipient
    message.set_content("A branch recovery was confirmed. Please view this message in an HTML client.")
    message.add_alternative(html_body, subtype="html")

    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=20) as server:
            server.starttls(context=ssl.create_default_context())
            server.login(smtp_email, smtp_password)
            server.send_message(message)
        logger.info(f"Recovery email dispatched to {recipient}.")
    except Exception as ex:
        logger.warning(f"Could not dispatch recovery email: {ex}")


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
