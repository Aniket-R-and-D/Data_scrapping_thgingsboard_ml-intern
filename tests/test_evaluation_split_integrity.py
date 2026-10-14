"""tests/test_evaluation_split_integrity.py — Stage D split integrity + leakage suite.

Verifies on the frozen manifests and JSONL splits in data/eval/:
1. No incident_id appears in more than one split (nor in incidents_table across splits).
2. No device appears in both TRAIN (or FUTURE_INCIDENTS_KNOWN_DEVICES) and UNSEEN_DEVICES.
3. No evaluation incident in FUTURE_INCIDENTS_KNOWN_DEVICES begins before
   train_cutoff_utc + purge_window, and no TRAIN incident ends after train_cutoff_utc.
4. No canonical_payload_hash or content_payload_hash appears in both TRAIN and
   either evaluation split, and no duplicates exist within any split.
5. Every record has a valid device_state, UTC ISO-8601 timestamp, policy_version
   ('ticket-policy-v1'), and incident_id, and its rendered deterministic ticket
   passes the Stage C field validator.
6. Manifest data_file_sha256 and manifest_sha256 match the actual files on disk,
   and all three manifests are marked frozen=True.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

from src.eval.verify_splits import MANIFESTS, _load_jsonl, _reconstruct_ticket_input, verify_all_splits
from src.tickets.field_validator import validate_candidate
from src.tickets.policy import DEVICE_STATES, POLICY_VERSION
from src.tickets.renderer import render_ticket

ROOT = Path(__file__).resolve().parents[1]
EVAL_DIR = ROOT / "data" / "eval"


@pytest.fixture(scope="module")
def splits():
    data = {}
    for sp, m_path in MANIFESTS.items():
        manifest = json.loads(m_path.read_text(encoding="utf-8"))
        records = _load_jsonl(ROOT / manifest["data_file"])
        data[sp] = (manifest, records)
    return data


def test_end_to_end_verifier_passes():
    report = verify_all_splits()
    assert report["verified"] is True
    assert report["error_count"] == 0


def test_no_incident_id_crosses_splits(splits):
    inc_train = {r["incident_id"] for r in splits["TRAIN"][1]}
    inc_future = {r["incident_id"] for r in splits["FUTURE_INCIDENTS_KNOWN_DEVICES"][1]}
    inc_unseen = {r["incident_id"] for r in splits["UNSEEN_DEVICES"][1]}
    assert not (inc_train & inc_future)
    assert not (inc_train & inc_unseen)
    assert not (inc_future & inc_unseen)

    inc_df = pd.read_parquet(EVAL_DIR / "incidents_table.parquet")
    assert inc_df["incident_id"].is_unique


def test_no_device_overlap_with_unseen_devices(splits):
    dev_train = {r["device_id"] for r in splits["TRAIN"][1]}
    dev_future = {r["device_id"] for r in splits["FUTURE_INCIDENTS_KNOWN_DEVICES"][1]}
    dev_unseen = {r["device_id"] for r in splits["UNSEEN_DEVICES"][1]}
    assert len(dev_unseen) == 27
    assert not (dev_train & dev_unseen)
    assert not (dev_future & dev_unseen)
    # FUTURE_INCIDENTS_KNOWN_DEVICES is specifically for known devices:
    assert len(dev_train & dev_future) > 0


def test_temporal_cutoff_and_purge_window_enforced(splits):
    train_manifest, train_recs = splits["TRAIN"]
    future_manifest, future_recs = splits["FUTURE_INCIDENTS_KNOWN_DEVICES"]
    cutoff = datetime.fromisoformat(train_manifest["train_cutoff_utc"])
    purge = timedelta(hours=int(train_manifest["purge_window_hours"]))
    eval_start = datetime.fromisoformat(future_manifest["eval_start_utc"])
    assert eval_start == cutoff + purge
    assert purge == timedelta(hours=24)

    for r in train_recs:
        assert datetime.fromisoformat(r["incident_end_utc"]) <= cutoff
        assert datetime.fromisoformat(r["prediction_timestamp_utc"]) <= cutoff
    for r in future_recs:
        assert datetime.fromisoformat(r["incident_start_utc"]) >= eval_start
        assert datetime.fromisoformat(r["prediction_timestamp_utc"]) >= eval_start


def test_no_payload_hash_duplication_within_or_across_splits(splits):
    for sp, (_, recs) in splits.items():
        p_hashes = [r["canonical_payload_hash"] for r in recs]
        c_hashes = [r["content_payload_hash"] for r in recs]
        assert len(p_hashes) == len(set(p_hashes)), f"within-split canonical dup in {sp}"
        assert len(c_hashes) == len(set(c_hashes)), f"within-split content dup in {sp}"

    train_p = {r["canonical_payload_hash"] for r in splits["TRAIN"][1]}
    train_c = {r["content_payload_hash"] for r in splits["TRAIN"][1]}
    for eval_sp in ("FUTURE_INCIDENTS_KNOWN_DEVICES", "UNSEEN_DEVICES"):
        eval_p = {r["canonical_payload_hash"] for r in splits[eval_sp][1]}
        eval_c = {r["content_payload_hash"] for r in splits[eval_sp][1]}
        assert not (train_p & eval_p), f"canonical payload leak into {eval_sp}"
        assert not (train_c & eval_c), f"content payload leak into {eval_sp}"


def test_every_record_valid_and_passes_stage_c_validator(splits):
    for sp, (_, recs) in splits.items():
        assert len(recs) > 0
        for r in recs:
            assert r["device_state"] in DEVICE_STATES
            assert r["policy_version"] == POLICY_VERSION
            assert r["incident_id"].startswith("INC-")
            dt = datetime.fromisoformat(r["prediction_timestamp_utc"])
            assert dt.tzinfo is not None
            ti = _reconstruct_ticket_input(r)
            rendered = render_ticket(ti)
            assert rendered.payload_hash == r["canonical_payload_hash"]
            assert rendered.text == r["deterministic_ticket"]
            vres = validate_candidate(rendered.text, ti)
            assert vres.is_valid, f"{sp}/{r['record_id']}: {vres.violations}"


def test_manifest_hashes_and_freeze_flags_match_files(splits):
    for sp, m_path in MANIFESTS.items():
        manifest = json.loads(m_path.read_text(encoding="utf-8"))
        assert manifest["frozen"] is True
        assert manifest["policy_version"] == POLICY_VERSION
        stored_sha = manifest["manifest_sha256"]
        body = {k: v for k, v in manifest.items() if k != "manifest_sha256"}
        recomputed_sha = hashlib.sha256(
            json.dumps(body, sort_keys=True, indent=2).encode("utf-8")
        ).hexdigest()
        assert stored_sha == recomputed_sha

        data_path = ROOT / manifest["data_file"]
        actual_data_sha = hashlib.sha256(data_path.read_bytes()).hexdigest()
        assert actual_data_sha == manifest["data_file_sha256"]
        assert manifest["record_count"] == len(splits[sp][1])
        assert manifest["unique_payload_count"] == manifest["record_count"]
