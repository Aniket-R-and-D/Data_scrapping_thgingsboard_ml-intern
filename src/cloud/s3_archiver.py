"""src/cloud/s3_archiver.py
Automated S3 Archival Engine for SEPLE NovaEdge AI Operations Reports.

Archives daily risk digests, recovery ledgers, and HTML executive briefs
to Amazon S3 (s3://bank-report-data/novaedge-ai-reports/).
"""

from __future__ import annotations

import logging
import mimetypes
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("s3_archiver")

DEFAULT_BUCKET = os.getenv("S3_BACKUP_BUCKET", "bank-report-data")
DEFAULT_PREFIX = os.getenv("S3_BACKUP_PREFIX", "novaedge-ai-reports")
AWS_REGION = os.getenv("AWS_REGION", "ap-south-1")


def get_s3_client():
    """Obtain boto3 S3 client using instance profile or environment credentials."""
    try:
        import boto3
        from botocore.config import Config
        cfg = Config(region_name=AWS_REGION, retries={"max_attempts": 3, "mode": "standard"})
        return boto3.client("s3", config=cfg)
    except Exception as e:
        logger.debug(f"Could not initialize boto3 S3 client: {e}")
        return None


def archive_reports_to_s3(
    reports_dir: Optional[Path] = None,
    bucket_name: Optional[str] = None,
    prefix: Optional[str] = None
) -> Dict[str, Any]:
    """Upload all daily digests, recovery ledgers, and latest summaries to S3."""
    root = Path(__file__).resolve().parent.parent.parent
    if reports_dir is None:
        reports_dir = root / "reports"

    bucket = bucket_name or DEFAULT_BUCKET
    base_prefix = (prefix or DEFAULT_PREFIX).strip("/")

    s3 = get_s3_client()
    if not s3:
        logger.warning("boto3 not available or AWS environment unconfigured; skipping S3 archival.")
        return {"uploaded": 0, "failed": 0, "files": []}

    if not reports_dir.exists():
        logger.warning(f"Reports directory {reports_dir} does not exist; skipping archival.")
        return {"uploaded": 0, "failed": 0, "files": []}

    logger.info(f"Starting S3 archival to s3://{bucket}/{base_prefix}/...")
    uploaded_files: List[str] = []
    failed_files: List[str] = []

    # Gather candidate files to archive
    target_patterns = [
        ("daily_risk_digest_*.*", f"{base_prefix}/daily_digests"),
        ("recovery_events_*.*", f"{base_prefix}/recovery_events"),
        ("latest_risk_digest.json", f"{base_prefix}/latest"),
        ("slack_payload_preview.json", f"{base_prefix}/latest"),
        ("data_quality_profile.json", f"{base_prefix}/quality"),
        ("model_comparison_benchmark.json", f"{base_prefix}/benchmarks"),
        ("telemetry_drift_report.json", f"{base_prefix}/quality"),
        ("recovery_debounce_state.json", f"{base_prefix}/quality"),
    ]

    for pattern, s3_subfolder in target_patterns:
        for file_path in reports_dir.glob(pattern):
            if not file_path.is_file():
                continue
            
            s3_key = f"{s3_subfolder}/{file_path.name}"
            content_type, _ = mimetypes.guess_type(str(file_path))
            if not content_type:
                content_type = "application/octet-stream"
            if file_path.suffix == ".html":
                content_type = "text/html; charset=utf-8"
            elif file_path.suffix == ".csv":
                content_type = "text/csv; charset=utf-8"
            elif file_path.suffix == ".json":
                content_type = "application/json"
            elif file_path.suffix == ".md":
                content_type = "text/markdown; charset=utf-8"

            extra_args = {
                "ContentType": content_type
            }

            try:
                s3.upload_file(
                    Filename=str(file_path),
                    Bucket=bucket,
                    Key=s3_key,
                    ExtraArgs=extra_args
                )
                uploaded_files.append(f"s3://{bucket}/{s3_key}")
                logger.debug(f"Uploaded: s3://{bucket}/{s3_key}")
            except Exception as e:
                logger.warning(f"Failed to upload {file_path.name} to s3://{bucket}/{s3_key}: {e}")
                failed_files.append(file_path.name)

    logger.info(
        f"✅ S3 Archival Complete: {len(uploaded_files)} files backed up to s3://{bucket}/{base_prefix}/, "
        f"{len(failed_files)} failures."
    )

    return {
        "bucket": bucket,
        "prefix": base_prefix,
        "uploaded_count": len(uploaded_files),
        "failed_count": len(failed_files),
        "uploaded_files": uploaded_files,
        "failed_files": failed_files
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    result = archive_reports_to_s3()
    print("\nSummary:")
    print(f"Bucket: {result['bucket']}")
    print(f"Uploaded: {result['uploaded_count']} files")
    for f in result["uploaded_files"][:5]:
        print(f"  - {f}")
