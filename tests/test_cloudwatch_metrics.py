"""tests/test_cloudwatch_metrics.py
Unit tests for CloudWatch metrics publisher and alarm telemetry.
"""

from unittest.mock import MagicMock, patch
import pytest

from src.cloud.cloudwatch_metrics import (
    publish_heartbeat,
    publish_quality_metrics,
    get_cloudwatch_client,
    NAMESPACE,
    DEFAULT_REGION
)


class TestCloudWatchMetrics:
    """Test suite for CloudWatch metrics emission."""

    @patch("boto3.client")
    def test_get_cloudwatch_client_default(self, mock_boto):
        mock_client = MagicMock()
        mock_boto.return_value = mock_client
        
        client = get_cloudwatch_client()
        mock_boto.assert_called_once_with("cloudwatch", region_name=DEFAULT_REGION)
        assert client == mock_client

    @patch("boto3.client")
    def test_get_cloudwatch_client_custom_region(self, mock_boto):
        mock_client = MagicMock()
        mock_boto.return_value = mock_client
        
        client = get_cloudwatch_client(region_name="us-east-1")
        mock_boto.assert_called_once_with("cloudwatch", region_name="us-east-1")
        assert client == mock_client

    @patch("src.cloud.cloudwatch_metrics.get_cloudwatch_client")
    def test_publish_heartbeat_probe_success(self, mock_get_cw):
        mock_cw = MagicMock()
        mock_get_cw.return_value = mock_cw
        
        result = publish_heartbeat(
            cycle_type="recovery_probe",
            success=True,
            restored_count=2
        )
        assert result is True
        mock_cw.put_metric_data.assert_called_once()
        
        call_kwargs = mock_cw.put_metric_data.call_args[1]
        assert call_kwargs["Namespace"] == NAMESPACE
        metric_data = call_kwargs["MetricData"]
        
        metric_names = [m["MetricName"] for m in metric_data]
        assert "Heartbeat" in metric_names
        assert "CycleSuccess" in metric_names
        assert "RestoredDevicesCount" in metric_names
        
        # Verify top-level heartbeat dimension
        top_heartbeats = [m for m in metric_data if m["MetricName"] == "Heartbeat" and len(m["Dimensions"]) == 1]
        assert len(top_heartbeats) == 1
        assert top_heartbeats[0]["Dimensions"][0]["Name"] == "Environment"
        assert top_heartbeats[0]["Value"] == 1.0

    @patch("src.cloud.cloudwatch_metrics.get_cloudwatch_client")
    def test_publish_heartbeat_daily_digest(self, mock_get_cw):
        mock_cw = MagicMock()
        mock_get_cw.return_value = mock_cw
        
        result = publish_heartbeat(
            cycle_type="daily_digest",
            success=True,
            restored_count=4,
            critical_count=2,
            high_risk_count=44
        )
        assert result is True
        
        call_kwargs = mock_cw.put_metric_data.call_args[1]
        metric_data = call_kwargs["MetricData"]
        metric_dict = {m["MetricName"]: m["Value"] for m in metric_data}
        
        assert metric_dict["Heartbeat"] == 1.0
        assert metric_dict["CycleSuccess"] == 1.0
        assert metric_dict["RestoredDevicesCount"] == 4.0
        assert metric_dict["CriticalDevicesCount"] == 2.0
        assert metric_dict["HighRiskDevicesCount"] == 44.0

    @patch("src.cloud.cloudwatch_metrics.get_cloudwatch_client")
    def test_publish_heartbeat_graceful_failure(self, mock_get_cw):
        mock_cw = MagicMock()
        mock_cw.put_metric_data.side_effect = RuntimeError("AWS Connection Timeout")
        mock_get_cw.return_value = mock_cw
        
        # Must not raise an exception, returns False
        result = publish_heartbeat(cycle_type="recovery_probe", success=False)
        assert result is False

    @patch("src.cloud.cloudwatch_metrics.get_cloudwatch_client")
    def test_publish_quality_metrics_drift_and_recovery(self, mock_get_cw):
        mock_cw = MagicMock()
        mock_get_cw.return_value = mock_cw

        drift_report = {
            "max_psi": 0.31,
            "drifted_signals": ["ac_voltage", "temperature"],
            "critical_signals": ["ac_voltage"],
            "signals": {"ac_voltage": {}, "temperature": {}, "battery_voltage": {}},
        }
        recovery_state = {
            "devices": {
                "dev-pending": {
                    "consecutive_nominal_cycles": 2,
                    "last_status": "PENDING_CONFIRMATION",
                },
                "dev-cleared": {
                    "consecutive_nominal_cycles": 3,
                    "last_status": "CONFIRMED_RECOVERED",
                    "cleared_at": "2026-10-27T06:45:00+00:00",
                },
            },
            "mttr_records": [
                {"bank": "BOI", "mttr_minutes": 45.0},
                {"bank": "SBI", "mttr_minutes": 75.0},
            ],
        }

        result = publish_quality_metrics(
            drift_report=drift_report,
            recovery_state=recovery_state,
            confirmed_recoveries=1,
        )

        assert result is True
        metric_data = mock_cw.put_metric_data.call_args[1]["MetricData"]
        metrics = {m["MetricName"]: m["Value"] for m in metric_data}

        assert metrics["DriftCriticalSignalCount"] == 1.0
        assert metrics["DriftModerateSignalCount"] == 1.0
        assert metrics["DriftMaxPSI"] == pytest.approx(0.31)
        assert metrics["DriftSignalsEvaluated"] == 3.0
        assert metrics["RecoveryPendingCount"] == 1.0
        assert metrics["RecoveryConfirmationInProgressCount"] == 1.0
        assert metrics["RecoveryConfirmedCount"] == 1.0
        assert metrics["RecoveryMTTRMeanMinutes"] == pytest.approx(60.0)

    @patch("src.cloud.cloudwatch_metrics.get_cloudwatch_client")
    def test_publish_quality_metrics_without_inputs_is_noop(self, mock_get_cw):
        assert publish_quality_metrics() is False
        mock_get_cw.assert_not_called()

    @patch("src.cloud.cloudwatch_metrics.get_cloudwatch_client")
    def test_publish_quality_metrics_graceful_failure(self, mock_get_cw):
        mock_cw = MagicMock()
        mock_cw.put_metric_data.side_effect = RuntimeError("throttled")
        mock_get_cw.return_value = mock_cw

        assert publish_quality_metrics(drift_report={"max_psi": 0.0}) is False
