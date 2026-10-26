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
