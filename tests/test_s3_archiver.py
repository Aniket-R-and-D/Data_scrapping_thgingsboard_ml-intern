"""Unit tests for src/cloud/s3_archiver.py."""

import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.cloud.s3_archiver import archive_reports_to_s3


class TestS3Archiver(unittest.TestCase):

    @patch("src.cloud.s3_archiver.get_s3_client")
    def test_archive_reports_mocked_s3(self, mock_get_client):
        mock_s3 = MagicMock()
        mock_get_client.return_value = mock_s3

        tmp_reports = Path("reports")
        result = archive_reports_to_s3(
            reports_dir=tmp_reports,
            bucket_name="test-bucket",
            prefix="test-prefix"
        )

        self.assertEqual(result["bucket"], "test-bucket")
        self.assertEqual(result["prefix"], "test-prefix")
        self.assertGreater(result["uploaded_count"], 0)
        self.assertEqual(result["failed_count"], 0)
        self.assertTrue(mock_s3.upload_file.called)

    @patch("src.cloud.s3_archiver.get_s3_client")
    def test_archive_reports_no_s3_client(self, mock_get_client):
        mock_get_client.return_value = None

        result = archive_reports_to_s3(
            reports_dir=Path("reports"),
            bucket_name="test-bucket"
        )
        self.assertEqual(result["uploaded"], 0)
        self.assertEqual(result["failed"], 0)


if __name__ == "__main__":
    unittest.main()
