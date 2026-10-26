#!/usr/bin/env python
"""send_daily_risk_digest.py
Option 2: Automated Morning AI Risk Digest & Operations Dispatcher.

Evaluates all 130 monitored bank branches against:
1. Isolation Forest Fleet Anomaly Scorer (365-day baseline models)
2. Gradient Boosting Electrical Outage Predictor (24h horizon, ROC-AUC 0.9997)

Multi-channel Operations Dispatch:
- ThingsBoard Live Sync: Pushes AI health tier, risk probabilities, and active alarms directly
  to ThingsBoard (https://seple.iot-private.cloud) so they appear on existing tenant dashboards.
- Slack Webhook: Rich Block-Kit notification with urgent alerts and deep-links.
- Email (SMTP): Executive HTML morning brief with color-coded risk tiers and CSV attachment.
- Local Archive: Generates Markdown, HTML, and CSV reports under reports/.

Usage:
  python send_daily_risk_digest.py [--dry-run] [--sync-tb] [--slack] [--email] [--top N]
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import smtplib
import ssl
import sys
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import joblib
import numpy as np
import pandas as pd
import requests
from dotenv import load_dotenv

from src.live.recovery_gate import probe_device_telemetry_live, RecoveryEvaluation
from src.live.alarm_clear_engine import (
    process_device_recovery_lifecycle,
    sync_recovery_attributes,
    get_active_ai_alarms,
    clear_ai_alarm
)

# Ensure safe UTF-8 output on Windows console
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
if sys.stderr.encoding and sys.stderr.encoding.lower() != "utf-8":
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Ensure logs directory exists
LOG_DIR = Path(__file__).resolve().parent / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_DIR / "daily_risk_digest.log", encoding="utf-8")
    ]
)
logger = logging.getLogger("daily_risk_digest")

ROOT = Path(__file__).resolve().parent
SCORED_ANOMALIES_PATH = ROOT / "training_data/anomaly_20261022_082325/scored_anomalies.parquet"
ANOMALY_FEATURES_PATH = ROOT / "training_data/anomaly_20261022_082325/anomaly_features.parquet"
REGISTRY_DEVICES_PATH = ROOT / "data_harvest/runs/20261022_082325/registry/devices.csv"
OUTAGE_MODEL_DIR = ROOT / "models/gbm_outage_oct2026_365d"
OPS_REVIEW_PACK_PATH = ROOT / "reports/ops_review_pack_top20_faulty_branches.json"
REPORTS_DIR = ROOT / "reports"


def load_environment() -> Dict[str, str]:
    """Load configuration from .env file."""
    load_dotenv(ROOT / ".env")
    return {
        "TB_HOST": os.getenv("TB_HOST", "https://seple.iot-private.cloud").rstrip("/"),
        "TB_EMAIL": os.getenv("TB_EMAIL", "info@seple.in"),
        "TB_PASSWORD": os.getenv("TB_PASSWORD", ""),
        "SLACK_WEBHOOK": os.getenv("SLACK_WEBHOOK", ""),
        "EMAIL_TO": os.getenv("EMAIL_TO", "animesh.choudhury@seple.in"),
        "SMTP_EMAIL": os.getenv("SMTP_EMAIL", "itinerant018@gmail.com"),
        "SMTP_PASSWORD": os.getenv("SMTP_PASSWORD", ""),
    }


def compute_fleet_risk(env: Optional[Dict[str, str]] = None) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """Compute anomaly tiers and predictive outage risk for all 130 branches."""
    logger.info("Loading latest anomaly scoring and feature representations...")
    fallback_scored = ROOT / "models/latest_fleet_scored.parquet"
    fallback_feats = ROOT / "models/latest_fleet_features.parquet"

    if SCORED_ANOMALIES_PATH.exists() and ANOMALY_FEATURES_PATH.exists():
        scored_df = pd.read_parquet(SCORED_ANOMALIES_PATH)
        feats_df = pd.read_parquet(ANOMALY_FEATURES_PATH)
        latest_scored = scored_df.sort_values("anchor_ts").groupby("device_id").last().reset_index()
        latest_feats = feats_df.sort_values("anchor_ts").groupby("device_id").last().reset_index()
    elif fallback_scored.exists() and fallback_feats.exists():
        logger.info(f"Loading packaged fleet baseline snapshots from {fallback_scored}...")
        latest_scored = pd.read_parquet(fallback_scored)
        latest_feats = pd.read_parquet(fallback_feats)
    else:
        raise FileNotFoundError(f"Missing anomaly datasets at {SCORED_ANOMALIES_PATH} and fallback models at {fallback_scored}")

    # Load Device Registry if available
    dev_names: Dict[str, str] = {}
    if REGISTRY_DEVICES_PATH.exists():
        try:
            devs_reg = pd.read_csv(REGISTRY_DEVICES_PATH)
            dev_names = devs_reg.set_index("device_id")["name"].to_dict()
        except Exception:
            pass

    # Load Outage Prediction Model
    outage_model_path = OUTAGE_MODEL_DIR / "model.joblib"
    feat_names_path = OUTAGE_MODEL_DIR / "feature_names.json"
    
    outage_probs = {}
    if outage_model_path.exists() and feat_names_path.exists():
        logger.info(f"Loading outage prediction model from {OUTAGE_MODEL_DIR}...")
        model = joblib.load(outage_model_path)
        with open(feat_names_path, "r", encoding="utf-8") as f:
            feature_names = json.load(f)

        X = latest_feats[feature_names].values
        probs = model.predict_proba(X)[:, 1]
        for dev_id, p in zip(latest_feats["device_id"], probs):
            outage_probs[dev_id] = float(p)
    else:
        logger.warning("Outage model not found; falling back to label status.")
        for dev_id in latest_feats["device_id"]:
            outage_probs[dev_id] = 0.0

    # Load Ops Review Pack if available for verified failure modes
    ops_pack_map: Dict[str, Dict[str, Any]] = {}
    if OPS_REVIEW_PACK_PATH.exists():
        try:
            with open(OPS_REVIEW_PACK_PATH, "r", encoding="utf-8") as f:
                ops_pack_list = json.load(f)
                for item in ops_pack_list:
                    ops_pack_map[item["device_id"]] = item
        except Exception as e:
            logger.debug(f"Could not load ops pack map: {e}")

    # Authenticate to ThingsBoard for Real-Time Telemetry Recovery Gate
    tb_token = None
    tb_host = None
    if env and env.get("TB_HOST") and env.get("TB_EMAIL") and env.get("TB_PASSWORD"):
        tb_host = env["TB_HOST"].rstrip("/")
        try:
            login_resp = requests.post(
                f"{tb_host}/api/auth/login",
                json={"username": env["TB_EMAIL"], "password": env["TB_PASSWORD"]},
                timeout=8
            )
            if login_resp.status_code == 200:
                tb_token = login_resp.json().get("token")
                logger.info("Connected to ThingsBoard for real-time recovery probing.")
        except Exception as e:
            logger.debug(f"Could not authenticate for recovery gate probing: {e}")

    # Merge into unified fleet frame
    records: List[Dict[str, Any]] = []
    for _, row in latest_scored.iterrows():
        dev_id = row["device_id"]
        dev_name = str(row.get("device_name") or dev_names.get(dev_id, dev_id))
        p_outage = outage_probs.get(dev_id, 0.0)
        anomaly_score = float(row["anomaly_score"])
        raw_tier = str(row["anomaly_tier"])
        contributions = row.get("top_contributions", {})

        # Determine Baseline Risk Tier
        if raw_tier == "CRITICAL_FAULT" or p_outage >= 0.80:
            unified_tier = "CRITICAL_FAULT"
        elif raw_tier == "ANOMALOUS" or p_outage >= 0.50:
            unified_tier = "HIGH_RISK"
        elif raw_tier == "ELEVATED" or p_outage >= 0.30:
            unified_tier = "ELEVATED"
        else:
            unified_tier = "NOMINAL"

        # Diagnose Primary Issue & Recommended Action
        top_issue = "Normal Baseline Operation"
        recommended_action = "Routine monitoring."

        # Check ops review pack first
        if dev_id in ops_pack_map:
            pack_item = ops_pack_map[dev_id]
            top_issue = pack_item.get("primary_failure_mode", top_issue)
            recommended_action = pack_item.get("recommended_field_action", recommended_action)
        elif isinstance(contributions, dict) and contributions:
            sorted_contribs = sorted(contributions.items(), key=lambda kv: kv[1], reverse=True)
            top_key, z_score = sorted_contribs[0]
            top_issue = f"{top_key} (z={z_score:.1f})"
            if "battery" in top_issue.lower():
                recommended_action = "Urgent: Inspect 12V backup battery pack; test internal resistance & replace if degraded."
            elif "ac_voltage" in top_issue.lower():
                recommended_action = "Electrical: High chronic undervoltage/brownouts; verify branch input phase lines."
            elif "system_current" in top_issue.lower():
                recommended_action = "Instrumentation: Verify CT current transformer wiring and sensor clamp calibration."
            elif "errorcount" in top_issue.lower() or "uptime" in top_issue.lower() or "gateway" in top_issue.lower():
                recommended_action = "Communications/Edge: High controller reset rate; verify 4G SIM antenna & power supply."
        elif unified_tier in ("CRITICAL_FAULT", "HIGH_RISK"):
            top_issue = f"High Predictive Collapse Risk ({p_outage * 100:.1f}%)"
            recommended_action = "Electrical & Edge: High probability of outage within 24h; verify branch mains power and battery."

        # Real-Time Telemetry Recovery Gate
        is_recovered = False
        recovery_vitals: Dict[str, Any] = {}
        if unified_tier in ("CRITICAL_FAULT", "HIGH_RISK") and tb_token and tb_host:
            try:
                rec_eval = probe_device_telemetry_live(dev_id, tb_host, tb_token)
                if rec_eval.is_recovered:
                    is_recovered = True
                    unified_tier = "SERVICED_RECOVERED"
                    top_issue = f"Hardware restored; {rec_eval.summary_reason}"
                    recommended_action = "None — Post-service operational stability verified"
                    recovery_vitals = rec_eval.vitals
                    logger.info(f"✅ Recovery Gate Confirmed: {dev_name} ({dev_id}) restored online!")
            except Exception as e:
                logger.debug(f"Recovery probe exception for {dev_id}: {e}")

        tb_device_url = f"https://seple.iot-private.cloud/devices/{dev_id}"

        records.append({
            "device_id": dev_id,
            "device_name": dev_name,
            "bank": row.get("bank", "UNKNOWN"),
            "unified_tier": unified_tier,
            "anomaly_score": round(anomaly_score, 4),
            "raw_anomaly_tier": raw_tier,
            "outage_prob_24h": round(p_outage, 4),
            "primary_issue": top_issue,
            "recommended_action": recommended_action,
            "thingsboard_url": tb_device_url,
            "last_evaluated": str(row["anchor_ts"]),
            "is_recovered": is_recovered,
            "recovery_vitals": json.dumps(recovery_vitals) if recovery_vitals else "",
        })

    fleet_df = pd.DataFrame(records)
    
    # Sort: CRITICAL first, then HIGH_RISK, then SERVICED_RECOVERED, then by outage_prob descending
    tier_order = {"CRITICAL_FAULT": 0, "HIGH_RISK": 1, "SERVICED_RECOVERED": 2, "ELEVATED": 3, "NOMINAL": 4}
    fleet_df["tier_rank"] = fleet_df["unified_tier"].map(tier_order).fillna(5)
    fleet_df = fleet_df.sort_values(["tier_rank", "outage_prob_24h", "anomaly_score"], ascending=[True, False, False]).drop(columns=["tier_rank"])

    # Aggregate Fleet Summary Stats
    total_branches = len(fleet_df)
    critical_count = (fleet_df["unified_tier"] == "CRITICAL_FAULT").sum()
    high_risk_count = (fleet_df["unified_tier"] == "HIGH_RISK").sum()
    recovered_count = (fleet_df["unified_tier"] == "SERVICED_RECOVERED").sum()
    elevated_count = (fleet_df["unified_tier"] == "ELEVATED").sum()
    nominal_count = (fleet_df["unified_tier"] == "NOMINAL").sum()
    fleet_health_score = round((nominal_count + recovered_count + (0.5 * elevated_count)) / total_branches * 100, 1)

    summary = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "total_monitored_branches": total_branches,
        "fleet_health_score_pct": fleet_health_score,
        "critical_fault_count": int(critical_count),
        "high_risk_count": int(high_risk_count),
        "serviced_recovered_count": int(recovered_count),
        "elevated_count": int(elevated_count),
        "nominal_count": int(nominal_count),
        "high_outage_risk_branches": int((fleet_df["outage_prob_24h"] >= 0.50).sum()),
    }

    return fleet_df, summary


def generate_reports(fleet_df: pd.DataFrame, summary: Dict[str, Any]) -> Tuple[Path, Path, Path]:
    """Generate Markdown, HTML, and CSV daily reports."""
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    today_str = datetime.now().strftime("%Y%m%d")

    csv_path = REPORTS_DIR / f"daily_risk_digest_{today_str}.csv"
    md_path = REPORTS_DIR / f"daily_risk_digest_{today_str}.md"
    html_path = REPORTS_DIR / f"daily_risk_digest_{today_str}.html"
    json_path = REPORTS_DIR / "latest_risk_digest.json"

    # Save CSV
    fleet_df.to_csv(csv_path, index=False)

    # Save recovery events CSV if any branches recovered
    recovered_df = fleet_df[fleet_df["unified_tier"] == "SERVICED_RECOVERED"]
    if not recovered_df.empty:
        rec_csv_path = REPORTS_DIR / f"recovery_events_{today_str}.csv"
        recovered_df.to_csv(rec_csv_path, index=False)
        logger.info(f"Saved {len(recovered_df)} verified recovery events to {rec_csv_path}")

    # Save JSON summary
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({
            "summary": summary,
            "top_critical": fleet_df[fleet_df["unified_tier"].isin(["CRITICAL_FAULT", "HIGH_RISK"])].to_dict(orient="records"),
            "recovered": recovered_df.to_dict(orient="records")
        }, f, indent=2)

    # Generate Markdown
    md_lines = [
        f"# ThingsBoard Daily AI Risk Digest — {datetime.now().strftime('%d %B %Y')}",
        "",
        f"**Monitored Fleet:** {summary['total_monitored_branches']} Bank Branches  ",
        f"**Fleet Health Score:** `{summary['fleet_health_score_pct']}%`  ",
        f"**Generated At:** `{summary['timestamp']}`  ",
        "",
        "## Fleet Executive Summary",
        "| Severity Tier | Branch Count | Operational Status |",
        "|---|:---:|---|",
        f"| 🚨 **CRITICAL FAULT** | **{summary['critical_fault_count']}** | Immediate physical intervention required |",
        f"| ⚠️ **HIGH RISK** | **{summary['high_risk_count']}** | Outage probability >= 50% within 24 hours |",
        f"| 🟢 **SERVICED / RESTORED** | **{summary.get('serviced_recovered_count', 0)}** | Hardware repaired; verified healthy via real-time telemetry |",
        f"| 🟡 **ELEVATED DRIFT** | **{summary['elevated_count']}** | Baseline electrical / comms deviation |",
        f"| ✅ **NOMINAL** | **{summary['nominal_count']}** | Operating within healthy baseline thresholds |",
        "",
    ]

    # Section for Recovered branches
    if not recovered_df.empty:
        md_lines.extend([
            "## ✅ Verified Field Recoveries (Hardware Restored)",
            "The following branches were previously at risk, but real-time telemetry confirms physical repair and nominal operation:",
            "",
            "| Rank | Bank | Branch Name | Status | Vitals & Restoration Detail | ThingsBoard Link |",
            "|:---:|:---:|---|:---:|---|:---:|",
        ])
        for idx, (_, r) in enumerate(recovered_df.iterrows(), start=1):
            md_lines.append(
                f"| {idx} | {r['bank']} | **{r['device_name']}** | `🟢 RESTORED` | {r['primary_issue']} | [View on TB]({r['thingsboard_url']}) |"
            )
        md_lines.append("")

    # Section for Critical / High Risk
    md_lines.extend([
        "## High-Risk & Critical Bank Branches",
        "| Rank | Bank | Branch Name | Unified Tier | 24h Outage Prob | Anomaly Score | Primary Root Cause | ThingsBoard Link |",
        "|:---:|:---:|---|:---:|:---:|:---:|---|:---:|",
    ])

    actionable = fleet_df[fleet_df["unified_tier"].isin(["CRITICAL_FAULT", "HIGH_RISK"])].head(25)
    for idx, (_, r) in enumerate(actionable.iterrows(), start=1):
        tier_badge = "🚨 CRITICAL" if r["unified_tier"] == "CRITICAL_FAULT" else "⚠️ HIGH"
        prob_str = f"{r['outage_prob_24h'] * 100:.1f}%"
        md_lines.append(
            f"| {idx} | {r['bank']} | **{r['device_name']}** | {tier_badge} | `{prob_str}` | `{r['anomaly_score']:.4f}` | {r['primary_issue']} | [View on TB]({r['thingsboard_url']}) |"
        )

    md_lines.extend([
        "",
        "## Recommended Technician Interventions",
    ])
    for idx, (_, r) in enumerate(actionable.head(10).iterrows(), start=1):
        md_lines.append(f"{idx}. **{r['device_name']}** ({r['bank']}): {r['recommended_action']}")

    md_lines.extend([
        "",
        "---",
        "*Automated AI Operations Dispatcher | SEPLE NovaEdge IoT AI Engine*"
    ])

    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(md_lines))

    # Generate Responsive HTML Email/Preview
    html_content = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #f8fafc; color: #1e293b; margin: 0; padding: 24px; }}
  .container {{ max-width: 880px; margin: 0 auto; background: #ffffff; border-radius: 12px; border: 1px solid #e2e8f0; overflow: hidden; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05); }}
  .header {{ background: linear-gradient(135deg, #0f172a 0%, #1e3a8a 100%); color: #ffffff; padding: 28px 32px; }}
  .header h1 {{ margin: 0 0 6px 0; font-size: 24px; font-weight: 700; }}
  .header p {{ margin: 0; opacity: 0.85; font-size: 14px; }}
  .metric-row {{ display: flex; border-bottom: 1px solid #e2e8f0; background: #f1f5f9; }}
  .metric-card {{ flex: 1; padding: 18px 20px; text-align: center; border-right: 1px solid #e2e8f0; }}
  .metric-card:last-child {{ border-right: none; }}
  .metric-val {{ font-size: 24px; font-weight: 800; margin-bottom: 4px; }}
  .metric-lbl {{ font-size: 11px; text-transform: uppercase; letter-spacing: 0.05em; color: #64748b; font-weight: 600; }}
  .content {{ padding: 28px 32px; }}
  h2 {{ font-size: 17px; font-weight: 700; margin: 24px 0 12px 0; color: #0f172a; border-left: 4px solid #2563eb; padding-left: 10px; }}
  table {{ width: 100%; border-collapse: collapse; margin-top: 12px; font-size: 13px; }}
  th {{ background: #f8fafc; color: #475569; font-weight: 600; text-align: left; padding: 10px 12px; border-bottom: 2px solid #e2e8f0; }}
  td {{ padding: 10px 12px; border-bottom: 1px solid #f1f5f9; vertical-align: middle; }}
  .badge-critical {{ background: #fee2e2; color: #991b1b; padding: 3px 8px; border-radius: 6px; font-weight: 700; font-size: 11px; display: inline-block; }}
  .badge-high {{ background: #fef3c7; color: #92400e; padding: 3px 8px; border-radius: 6px; font-weight: 700; font-size: 11px; display: inline-block; }}
  .badge-recovered {{ background: #d1fae5; color: #065f46; padding: 3px 8px; border-radius: 6px; font-weight: 700; font-size: 11px; display: inline-block; }}
  .badge-normal {{ background: #dcfce7; color: #166534; padding: 3px 8px; border-radius: 6px; font-weight: 700; font-size: 11px; display: inline-block; }}
  .btn-link {{ background: #2563eb; color: #ffffff !important; text-decoration: none; padding: 4px 10px; border-radius: 6px; font-size: 11px; font-weight: 600; display: inline-block; }}
  .action-box {{ background: #eff6ff; border-left: 4px solid #3b82f6; padding: 16px; border-radius: 6px; margin-top: 20px; font-size: 13px; line-height: 1.6; }}
  .footer {{ padding: 18px 32px; background: #f8fafc; font-size: 12px; color: #94a3b8; text-align: center; border-top: 1px solid #e2e8f0; }}
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <h1>ThingsBoard Morning AI Risk Digest</h1>
    <p>Operational Fleet Assessment &bull; {datetime.now().strftime('%A, %d %B %Y')} &bull; SEPLE NovaEdge AI</p>
  </div>
  <div class="metric-row">
    <div class="metric-card">
      <div class="metric-val" style="color: #2563eb;">{summary['total_monitored_branches']}</div>
      <div class="metric-lbl">Monitored Branches</div>
    </div>
    <div class="metric-card">
      <div class="metric-val" style="color: #dc2626;">{summary['critical_fault_count']}</div>
      <div class="metric-lbl">Critical Faults</div>
    </div>
    <div class="metric-card">
      <div class="metric-val" style="color: #d97706;">{summary['high_risk_count']}</div>
      <div class="metric-lbl">High 24h Risk</div>
    </div>
    <div class="metric-card">
      <div class="metric-val" style="color: #059669;">{summary.get('serviced_recovered_count', 0)}</div>
      <div class="metric-lbl">Serviced & Restored</div>
    </div>
    <div class="metric-card">
      <div class="metric-val" style="color: #16a34a;">{summary['fleet_health_score_pct']}%</div>
      <div class="metric-lbl">Fleet Health Index</div>
    </div>
  </div>
  <div class="content">
"""

    if not recovered_df.empty:
        html_content += f"""    <h2>✅ Verified Branch Recoveries (Past 24 Hours)</h2>
    <p style="font-size: 13px; color: #64748b; margin-top: -6px;">The following branches were previously at risk, but live telemetry confirms physical repair and nominal vital metrics:</p>
    <table>
      <thead>
        <tr>
          <th>Branch Name</th>
          <th>Bank</th>
          <th>Status</th>
          <th>Verified Restoration Detail</th>
          <th>ThingsBoard</th>
        </tr>
      </thead>
      <tbody>
"""
        for _, r in recovered_df.iterrows():
            html_content += f"""        <tr>
          <td><strong>{r['device_name']}</strong></td>
          <td>{r['bank']}</td>
          <td><span class="badge-recovered">RESTORED ONLINE</span></td>
          <td style="color: #047857; font-size: 12px;">{r['primary_issue']}</td>
          <td><a href="{r['thingsboard_url']}" class="btn-link" target="_blank">Open TB &rarr;</a></td>
        </tr>
"""
        html_content += """      </tbody>
    </table>
"""

    html_content += f"""    <h2>Immediate Physical & Electrical Risk Alerts</h2>
    <p style="font-size: 13px; color: #64748b; margin-top: -6px;">The following branches show ongoing baseline anomalies or electrical collapse probabilities exceeding 50% within the next 24 hours.</p>
    <table>
      <thead>
        <tr>
          <th>Branch Name</th>
          <th>Bank</th>
          <th>Risk Tier</th>
          <th>24h Outage Prob</th>
          <th>Primary Root Cause</th>
          <th>ThingsBoard</th>
        </tr>
      </thead>
      <tbody>
"""
    for _, r in actionable.head(15).iterrows():
        b_class = "badge-critical" if r["unified_tier"] == "CRITICAL_FAULT" else "badge-high"
        html_content += f"""        <tr>
          <td><strong>{r['device_name']}</strong></td>
          <td>{r['bank']}</td>
          <td><span class="{b_class}">{r['unified_tier']}</span></td>
          <td style="font-weight: 700; color: #b91c1c;">{r['outage_prob_24h'] * 100:.1f}%</td>
          <td style="color: #475569; font-size: 12px;">{r['primary_issue']}</td>
          <td><a href="{r['thingsboard_url']}" class="btn-link" target="_blank">Open TB &rarr;</a></td>
        </tr>
"""
    html_content += f"""      </tbody>
    </table>

    <h2>Priority Field Operations Dispatch</h2>
    <div class="action-box">
      <ol style="margin: 0; padding-left: 20px;">
"""
    for _, r in actionable.head(5).iterrows():
        html_content += f"        <li style='margin-bottom: 6px;'><strong>{r['device_name']} ({r['bank']}):</strong> {r['recommended_action']}</li>\n"
    
    html_content += f"""      </ol>
    </div>
    <p style="margin-top: 18px; font-size: 12px; color: #64748b;">
      Full report of all 130 branches attached as CSV. You can also view live analytics on the 
      <a href="https://seple.iot-private.cloud/dashboards/4a25a020-62f7-11f1-9463-fde182b082f4" style="color: #2563eb; font-weight: 600;">ThingsBoard AI Fleet Operations Dashboard</a>.
    </p>
  </div>
  <div class="footer">
    SEPLE NovaEdge Advanced IoT Machine Learning Engine &bull; Automated Operations Service
  </div>
</div>
</body>
</html>
"""
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html_content)

    # Automated S3 Cloud Archival
    try:
        from src.cloud.s3_archiver import archive_reports_to_s3
        archive_reports_to_s3(REPORTS_DIR)
    except Exception as e:
        logger.debug(f"S3 archival skipped or encountered: {e}")

    return md_path, html_path, csv_path


def sync_to_thingsboard(fleet_df: pd.DataFrame, env: Dict[str, str], max_sync: int = 130) -> None:
    """Sync AI attributes and trigger active alarms on ThingsBoard platform."""
    host = env["TB_HOST"]
    email = env["TB_EMAIL"]
    password = env["TB_PASSWORD"]

    if not email or not password:
        logger.warning("ThingsBoard credentials not provided in .env; skipping ThingsBoard sync.")
        return

    logger.info(f"Authenticating with ThingsBoard at {host}...")
    try:
        login_resp = requests.post(f"{host}/api/auth/login", json={"username": email, "password": password}, timeout=15)
        if login_resp.status_code != 200:
            logger.error(f"ThingsBoard login failed: {login_resp.status_code} - {login_resp.text}")
            return
        token = login_resp.json().get("token")
        headers = {"X-Authorization": f"Bearer {token}"}
    except Exception as e:
        logger.error(f"Error connecting to ThingsBoard: {e}")
        return

    logger.info("Syncing AI attributes, clearing recovered alarms, and registering active alarms...")
    synced_attrs = 0
    synced_alarms = 0
    cleared_alarms = 0
    now_iso = datetime.now(timezone.utc).isoformat()

    for _, row in fleet_df.head(max_sync).iterrows():
        dev_id = row["device_id"]
        tier = row["unified_tier"]
        p_outage = float(row["outage_prob_24h"])
        score = float(row["anomaly_score"])
        issue = str(row["primary_issue"])
        action = str(row["recommended_action"])

        # If device is SERVICED_RECOVERED, clear any active AI alarms on ThingsBoard!
        if tier == "SERVICED_RECOVERED":
            try:
                active_ai_alarms = get_active_ai_alarms(dev_id, host, token)
                for a in active_ai_alarms:
                    a_id = a.get("id", {}).get("id")
                    if a_id:
                        if clear_ai_alarm(a_id, host, token):
                            cleared_alarms += 1
            except Exception as e:
                logger.debug(f"Exception clearing alarms for {dev_id}: {e}")

        # 1. Push server-side and shared attributes & telemetry
        attrs_payload = {
            "ai_health_tier": tier,
            "ai_anomaly_score": score,
            "ai_outage_risk_prob": p_outage,
            "ai_primary_issue": issue,
            "ai_recommended_action": action,
            "ai_last_evaluated": now_iso,
        }
        try:
            # Server scope
            r = requests.post(f"{host}/api/plugins/telemetry/DEVICE/{dev_id}/SERVER_SCOPE", json=attrs_payload, headers=headers, timeout=5)
            # Shared scope (for dashboard entity tables)
            requests.post(f"{host}/api/plugins/telemetry/DEVICE/{dev_id}/SHARED_SCOPE", json=attrs_payload, headers=headers, timeout=5)
            # Timeseries
            requests.post(f"{host}/api/plugins/telemetry/DEVICE/{dev_id}/timeseries/any", json=attrs_payload, headers=headers, timeout=5)
            if r.status_code == 200:
                synced_attrs += 1
        except Exception as e:
            logger.debug(f"Failed to push attributes for {dev_id}: {e}")

        # 2. Trigger active alarm for CRITICAL_FAULT or Outage Prob >= 0.50 (ONLY if NOT recovered)
        if (tier == "CRITICAL_FAULT" or p_outage >= 0.50) and tier != "SERVICED_RECOVERED":
            severity = "CRITICAL" if (tier == "CRITICAL_FAULT" or p_outage >= 0.75) else "MAJOR"
            alarm_type = "AI_CRITICAL_ANOMALY" if tier == "CRITICAL_FAULT" else "AI_PREDICTIVE_OUTAGE_RISK"
            alarm_payload = {
                "originator": {
                    "entityType": "DEVICE",
                    "id": dev_id
                },
                "type": alarm_type,
                "severity": severity,
                "details": {
                    "predicted_outage_risk_24h": p_outage,
                    "anomaly_score": score,
                    "primary_issue": issue,
                    "recommended_action": action,
                    "evaluated_at": now_iso
                }
            }
            try:
                ar = requests.post(f"{host}/api/alarm", json=alarm_payload, headers=headers, timeout=5)
                if ar.status_code == 200:
                    synced_alarms += 1
            except Exception as e:
                logger.debug(f"Failed to create alarm for {dev_id}: {e}")

    logger.info(
        f"ThingsBoard sync complete: {synced_attrs} devices updated, "
        f"{cleared_alarms} recovered alarms cleared, {synced_alarms} active alarms registered."
    )


def send_slack_notification(fleet_df: pd.DataFrame, summary: Dict[str, Any], env: Dict[str, str]) -> bool:
    """Send Block-Kit formatted alert digest to Slack."""
    webhook_url = env.get("SLACK_WEBHOOK", "").strip()
    if not webhook_url:
        logger.warning("SLACK_WEBHOOK is not set; skipping Slack dispatch.")
        return False

    # Check if webhook URL is valid incoming webhook
    if not webhook_url.startswith("https://hooks.slack.com/services/"):
        logger.warning(
            f"SLACK_WEBHOOK '{webhook_url}' appears to be a user profile link rather than an Incoming Webhook URL.\n"
            f"To enable direct channel notifications, create an Incoming Webhook at https://api.slack.com/apps."
        )

    # Build Slack Block Kit
    actionable = fleet_df[fleet_df["unified_tier"].isin(["CRITICAL_FAULT", "HIGH_RISK"])].head(6)
    recovered = fleet_df[fleet_df["unified_tier"] == "SERVICED_RECOVERED"].head(5)
    
    blocks: List[Dict[str, Any]] = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"🌅 ThingsBoard Morning AI Risk Digest — {datetime.now().strftime('%d %b %Y')}",
                "emoji": True
            }
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*Fleet Status:* `{summary['total_monitored_branches']}` Branches Monitored | "
                    f"*Health Index:* `{summary['fleet_health_score_pct']}%`\n"
                    f"🚨 *Critical Faults:* `{summary['critical_fault_count']}` | "
                    f"⚠️ *High 24h Risk:* `{summary['high_risk_count']}` | "
                    f"🟢 *Restored:* `{summary.get('serviced_recovered_count', 0)}` | "
                    f"✅ *Nominal:* `{summary['nominal_count']}`"
                )
            }
        }
    ]

    # Add Verified Recoveries Section if any exist
    if not recovered.empty:
        blocks.extend([
            {"type": "divider"},
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "*✅ Verified Branch Recoveries (Past 24h) — Hardware Restored:*"
                }
            }
        ])
        for _, r in recovered.iterrows():
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"🟢 *<{r['thingsboard_url']}|{r['device_name']}>* ({r['bank']})\n"
                        f"• *Status:* Restored & Telemetry Verified Healthy | *Alarms:* Cleared\n"
                        f"• *Vitals:* {r['primary_issue']}"
                    )
                }
            })

    # Add Actionable Critical Alerts Section
    blocks.extend([
        {"type": "divider"},
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "*🚨 Priority Branches Requiring Immediate Field Intervention:*"
            }
        }
    ])

    for _, r in actionable.iterrows():
        tier_icon = "🔴" if r["unified_tier"] == "CRITICAL_FAULT" else "🟠"
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"{tier_icon} *<{r['thingsboard_url']}|{r['device_name']}>* ({r['bank']})\n"
                    f"• *24h Outage Risk:* `{r['outage_prob_24h'] * 100:.1f}%` | *Anomaly Score:* `{r['anomaly_score']:.4f}`\n"
                    f"• *Primary Issue:* {r['primary_issue']}\n"
                    f"• *Action:* {r['recommended_action']}"
                )
            }
        })

    blocks.extend([
        {"type": "divider"},
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "📊 Open ThingsBoard AI Dashboard"},
                    "url": "https://seple.iot-private.cloud/dashboards/4a25a020-62f7-11f1-9463-fde182b082f4",
                    "style": "primary"
                }
            ]
        }
    ])

    payload = {"blocks": blocks}

    # Save Slack payload preview for offline inspection
    with open(REPORTS_DIR / "slack_payload_preview.json", "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

    try:
        resp = requests.post(webhook_url, json=payload, timeout=10)
        if resp.status_code == 200:
            logger.info("✅ Slack notification delivered successfully.")
            return True
        else:
            logger.warning(f"Slack webhook returned status {resp.status_code}: {resp.text[:150]}")
            return False
    except Exception as e:
        logger.warning(f"Failed to post to Slack: {e}")
        return False


def send_email_notification(html_path: Path, csv_path: Path, summary: Dict[str, Any], env: Dict[str, str]) -> bool:
    """Send HTML executive risk digest with CSV attachment via SMTP."""
    smtp_email = env.get("SMTP_EMAIL", "").strip()
    smtp_pw = env.get("SMTP_PASSWORD", "").strip()
    recipient = env.get("EMAIL_TO", "animesh.choudhury@seple.in").strip()

    if not smtp_email or not smtp_pw:
        logger.warning("SMTP_EMAIL or SMTP_PASSWORD not set in .env; skipping email dispatch.")
        return False

    with open(html_path, "r", encoding="utf-8") as f:
        html_body = f.read()

    msg = EmailMessage()
    today_str = datetime.now().strftime("%d %b %Y")
    recovered_txt = f" / {summary.get('serviced_recovered_count', 0)} Restored" if summary.get('serviced_recovered_count', 0) > 0 else ""
    msg["Subject"] = f"[ThingsBoard AI Digest] {today_str} — {summary['critical_fault_count']} Critical / {summary['high_risk_count']} High Risk{recovered_txt} Branches"
    msg["From"] = smtp_email
    msg["To"] = recipient

    # Set content
    msg.set_content(
        f"ThingsBoard Daily AI Risk Digest — {today_str}\n"
        f"Fleet Health Score: {summary['fleet_health_score_pct']}%\n"
        f"Critical Faults: {summary['critical_fault_count']}\n"
        f"High 24h Outage Risk: {summary['high_risk_count']}\n"
        f"Serviced / Restored: {summary.get('serviced_recovered_count', 0)}\n"
        f"Elevated Drift: {summary['elevated_count']}\n"
        f"Nominal: {summary['nominal_count']}\n\n"
        "Please view the attached HTML summary and CSV report for detailed branch-level diagnostics."
    )
    msg.add_alternative(html_body, subtype="html")

    # Attach CSV
    if csv_path.exists():
        with open(csv_path, "rb") as f:
            msg.add_attachment(
                f.read(),
                maintype="text",
                subtype="csv",
                filename=csv_path.name
            )

    logger.info(f"Dispatching email via smtp.gmail.com to {recipient}...")
    try:
        # Try TLS on 587
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=15) as s:
            s.starttls()
            s.login(smtp_email, smtp_pw)
            s.send_message(msg)
        logger.info(f"✅ Email delivered successfully to {recipient}.")
        return True
    except Exception as e:
        logger.warning(
            f"Gmail SMTP dispatch encountered: {e}\n"
            f"Note: If using Gmail, an App Password (16 characters) is required at https://myaccount.google.com/apppasswords.\n"
            f"HTML email report is preserved at file:///{html_path.resolve()} for direct viewing."
        )
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description="ThingsBoard Daily AI Operations & Risk Dispatcher")
    parser.add_argument("--dry-run", action="store_true", help="Evaluate fleet and print preview without sending network dispatches")
    parser.add_argument("--sync-tb", action="store_true", help="Sync attributes and alarms to ThingsBoard platform")
    parser.add_argument("--slack", action="store_true", help="Dispatch Slack notification")
    parser.add_argument("--email", action="store_true", help="Dispatch Email notification")
    parser.add_argument("--top", type=int, default=10, help="Number of priority branches to display in console")
    args = parser.parse_args()

    env = load_environment()

    logger.info("=" * 65)
    logger.info("Starting Daily AI Operations & Risk Digest...")
    logger.info("=" * 65)

    # 1. Compute Risk (with Live Telemetry Recovery Probing)
    fleet_df, summary = compute_fleet_risk(env)

    # 2. Generate Reports
    md_path, html_path, csv_path = generate_reports(fleet_df, summary)
    logger.info(f"Daily reports generated:")
    logger.info(f"  Markdown: {md_path}")
    logger.info(f"  HTML:     {html_path}")
    logger.info(f"  CSV:      {csv_path}")

    # 3. Print Console Summary
    print("\n" + "=" * 65)
    print(f"📊 FLEET EXECUTIVE DIGEST — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 65)
    print(f"Total Monitored Branches: {summary['total_monitored_branches']}")
    print(f"Fleet Health Score:       {summary['fleet_health_score_pct']}%")
    print(f"Critical Fault Branches:  {summary['critical_fault_count']}")
    print(f"High Outage Risk (>=50%): {summary['high_risk_count']}")
    print(f"Serviced / Restored:      {summary.get('serviced_recovered_count', 0)}")
    print(f"Elevated Drift Branches:  {summary['elevated_count']}")
    print(f"Nominal / Healthy:        {summary['nominal_count']}")
    print("-" * 65)
    print("TOP PRIORITY BRANCHES REQUIRING ATTENTION:")
    priority = fleet_df[fleet_df["unified_tier"].isin(["CRITICAL_FAULT", "HIGH_RISK"])].head(args.top)
    for idx, (_, r) in enumerate(priority.iterrows(), 1):
        print(f" {idx:2d}. [{r['unified_tier']:14s}] {r['device_name']:22s} ({r['bank']:6s}) | Outage Risk: {r['outage_prob_24h']*100:5.1f}% | {r['primary_issue']}")
    
    recovered = fleet_df[fleet_df["unified_tier"] == "SERVICED_RECOVERED"]
    if not recovered.empty:
        print("-" * 65)
        print("VERIFIED POST-SERVICE RECOVERIES (ALERTS CLEARED):")
        for idx, (_, r) in enumerate(recovered.iterrows(), 1):
            print(f" {idx:2d}. [RESTORED] {r['device_name']:22s} ({r['bank']:6s}) | {r['primary_issue']}")
    print("=" * 65 + "\n")

    if args.dry_run:
        logger.info("Dry-run mode active; skipping live network dispatches.")
        return

    # 4. Multi-channel Dispatches
    # ThingsBoard Sync
    should_sync_tb = args.sync_tb or (not args.slack and not args.email)
    if should_sync_tb:
        sync_to_thingsboard(fleet_df, env)

    # Slack Dispatch
    should_slack = args.slack or (not args.sync_tb and not args.email)
    if should_slack:
        send_slack_notification(fleet_df, summary, env)

    # Email Dispatch
    should_email = args.email or (not args.sync_tb and not args.slack)
    if should_email:
        send_email_notification(html_path, csv_path, summary, env)

    logger.info("Daily AI Operations & Risk Digest run completed successfully.")


if __name__ == "__main__":
    main()
