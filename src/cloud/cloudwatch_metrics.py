"""src/cloud/cloudwatch_metrics.py
CloudWatch Metrics Publisher for SEPLE NovaEdge AI Engine.

Emits operational health, cycle heartbeats, and fleet recovery telemetry
to Amazon CloudWatch under the 'NovaEdge/FleetAgent' namespace.
Enables CloudWatch Alarms to detect if the container stops responding
(missing heartbeats) or if critical failure spikes occur.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("cloudwatch_metrics")

NAMESPACE = os.getenv("CLOUDWATCH_NAMESPACE", "NovaEdge/FleetAgent")
DEFAULT_REGION = os.getenv("AWS_DEFAULT_REGION", os.getenv("AWS_REGION", "ap-south-1"))


def get_cloudwatch_client(region_name: Optional[str] = None):
    """Obtain a boto3 CloudWatch client using instance profile or environment credentials."""
    import boto3
    return boto3.client("cloudwatch", region_name=region_name or DEFAULT_REGION)


def publish_heartbeat(
    cycle_type: str = "heartbeat",
    success: bool = True,
    restored_count: int = 0,
    critical_count: int = 0,
    high_risk_count: int = 0,
    region_name: Optional[str] = None
) -> bool:
    """Publish daemon cycle execution telemetry and heartbeat to CloudWatch.
    
    Args:
        cycle_type: Type of cycle ('recovery_probe', 'daily_digest', or 'heartbeat').
        success: Whether the cycle finished cleanly without fatal uncaught exceptions.
        restored_count: Number of devices confirmed restored in this cycle.
        critical_count: Number of critical risk devices found.
        high_risk_count: Number of high risk devices found.
        region_name: Optional AWS region override.
        
    Returns:
        True if metrics published successfully, False otherwise.
    """
    environment = os.getenv("ENVIRONMENT", "production")
    
    try:
        cw = get_cloudwatch_client(region_name)
        now = datetime.now(timezone.utc)
        
        env_dimensions = [
            {"Name": "Environment", "Value": environment}
        ]
        cycle_dimensions = [
            {"Name": "Environment", "Value": environment},
            {"Name": "CycleType", "Value": cycle_type}
        ]
        
        metric_data = [
            # Top-level daemon heartbeat (for MissingHeartbeat alarm)
            {
                "MetricName": "Heartbeat",
                "Dimensions": env_dimensions,
                "Timestamp": now,
                "Value": 1.0,
                "Unit": "Count"
            },
            # Cycle-specific heartbeat
            {
                "MetricName": "Heartbeat",
                "Dimensions": cycle_dimensions,
                "Timestamp": now,
                "Value": 1.0,
                "Unit": "Count"
            },
            {
                "MetricName": "CycleSuccess",
                "Dimensions": cycle_dimensions,
                "Timestamp": now,
                "Value": 1.0 if success else 0.0,
                "Unit": "Count"
            },
            {
                "MetricName": "RestoredDevicesCount",
                "Dimensions": cycle_dimensions,
                "Timestamp": now,
                "Value": float(restored_count),
                "Unit": "Count"
            }
        ]
        
        if critical_count > 0 or cycle_type == "daily_digest":
            metric_data.append({
                "MetricName": "CriticalDevicesCount",
                "Dimensions": cycle_dimensions,
                "Timestamp": now,
                "Value": float(critical_count),
                "Unit": "Count"
            })
            
        if high_risk_count > 0 or cycle_type == "daily_digest":
            metric_data.append({
                "MetricName": "HighRiskDevicesCount",
                "Dimensions": cycle_dimensions,
                "Timestamp": now,
                "Value": float(high_risk_count),
                "Unit": "Count"
            })
            
        cw.put_metric_data(Namespace=NAMESPACE, MetricData=metric_data)
        logger.info(f"Published CloudWatch metrics to {NAMESPACE} (Cycle: {cycle_type}, Success: {success}, Restored: {restored_count})")
        return True
    except Exception as e:
        logger.warning(f"Could not publish CloudWatch metrics: {e}")
        return False


def publish_quality_metrics(
    drift_report: Optional[dict] = None,
    recovery_state: Optional[dict] = None,
    confirmed_recoveries: int = 0,
    region_name: Optional[str] = None
) -> bool:
    """Publish telemetry-drift and debounced-recovery quality counters.

    These feed the Phase 7 quality alarms: sustained drift, a stalled debounce
    queue, and recovery volume / MTTR regressions.

    Args:
        drift_report: Output of ``src.features.drift_monitor.evaluate_drift``.
        recovery_state: Persisted debounce state (``reports/recovery_debounce_state.json``).
        confirmed_recoveries: Recoveries confirmed during the current cycle.
        region_name: Optional AWS region override.

    Returns:
        True if metrics published successfully, False otherwise.
    """
    if drift_report is None and recovery_state is None:
        return False

    environment = os.getenv("ENVIRONMENT", "production")

    try:
        cw = get_cloudwatch_client(region_name)
        now = datetime.now(timezone.utc)
        dimensions = [{"Name": "Environment", "Value": environment}]

        def _metric(name: str, value: float, unit: str = "Count") -> dict:
            return {
                "MetricName": name,
                "Dimensions": dimensions,
                "Timestamp": now,
                "Value": float(value),
                "Unit": unit,
            }

        metric_data = []

        if drift_report is not None:
            signals = drift_report.get("signals") or {}
            drifted = drift_report.get("drifted_signals") or []
            critical = drift_report.get("critical_signals") or []
            metric_data.extend(
                [
                    _metric("DriftCriticalSignalCount", len(critical)),
                    _metric("DriftModerateSignalCount", max(0, len(drifted) - len(critical))),
                    _metric("DriftMaxPSI", float(drift_report.get("max_psi") or 0.0), "None"),
                    _metric("DriftedSignalCount", len(drifted)),
                    _metric("DriftSignalsEvaluated", len(signals)),
                ]
            )

        if recovery_state is not None:
            devices = recovery_state.get("devices") or {}
            pending = [
                record
                for record in devices.values()
                if not record.get("cleared_at")
                and record.get("last_status") != "CONFIRMED_RECOVERED"
            ]
            stalled = [
                record
                for record in pending
                if int(record.get("consecutive_nominal_cycles", 0)) > 0
            ]
            mttr_values = [
                float(record["mttr_minutes"])
                for record in (recovery_state.get("mttr_records") or [])
                if isinstance(record.get("mttr_minutes"), (int, float))
            ]
            mean_mttr = sum(mttr_values) / len(mttr_values) if mttr_values else 0.0

            metric_data.extend(
                [
                    _metric("RecoveryPendingCount", len(pending)),
                    _metric("RecoveryConfirmationInProgressCount", len(stalled)),
                    _metric("RecoveryConfirmedCount", confirmed_recoveries),
                    _metric("RecoveryMTTRMeanMinutes", mean_mttr, "None"),
                ]
            )

        cw.put_metric_data(Namespace=NAMESPACE, MetricData=metric_data)
        logger.info(
            f"Published {len(metric_data)} quality metrics to {NAMESPACE} "
            f"(confirmed recoveries: {confirmed_recoveries})."
        )
        return True
    except Exception as e:
        logger.warning(f"Could not publish CloudWatch quality metrics: {e}")
        return False


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(f"Testing CloudWatch metric publication to namespace '{NAMESPACE}'...")
    ok = publish_heartbeat(
        cycle_type="test_probe",
        success=True,
        restored_count=4,
        critical_count=1,
        high_risk_count=45
    )
    if ok:
        print("[SUCCESS] CloudWatch test metric published successfully!")
    else:
        print("[ERROR] CloudWatch test metric publication failed.")
