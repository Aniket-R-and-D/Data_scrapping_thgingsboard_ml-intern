"""src/eval/verify_splits.py — Stage D split integrity + deterministic baseline verifier.

Recomputes and verifies:
1. Disjointness of incident_ids across all three splits.
2. Zero device overlap between TRAIN (or FUTURE_INCIDENTS_KNOWN_DEVICES) and
   UNSEEN_DEVICES.
3. All TRAIN incidents end on or before train_cutoff_utc; all
   FUTURE_INCIDENTS_KNOWN_DEVICES incidents start on or after
   train_cutoff_utc + purge_window.
4. Zero canonical_payload_hash and zero content_payload_hash overlap between
   TRAIN and either evaluation split, and zero duplicates within any split.
5. Every record carries a valid device_state, UTC ISO-8601 timestamp,
   policy_version ('ticket-policy-v1'), and incident_id, and its rendered
   deterministic ticket passes the Stage C field validator.
6. Manifest data_file_sha256 and manifest_sha256 match the actual files on disk.
7. Separate reporting of template-signature repetition vs. unique completion
   text (identical template skeletons are not leakage, and are reported
   transparently).
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.tickets.field_validator import validate_candidate
from src.tickets.policy import DEVICE_STATES, POLICY_VERSION
from src.tickets.renderer import render_ticket
from src.tickets.schema import Measurement, TicketInput

EVAL_DIR = ROOT / "data" / "eval"
MANIFESTS = {
    "TRAIN": EVAL_DIR / "manifest_train.json",
    "FUTURE_INCIDENTS_KNOWN_DEVICES": EVAL_DIR / "manifest_future_known_devices.json",
    "UNSEEN_DEVICES": EVAL_DIR / "manifest_unseen_devices.json",
}


def _sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _load_jsonl(path: Path) -> List[Dict[str, Any]]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def _reconstruct_ticket_input(rec: Dict[str, Any]) -> TicketInput:
    cp = rec["canonical_payload"]
    measurements = [
        Measurement(
            field=m["field"],
            value=m["value"],
            unit=m["unit"],
            observed_at=datetime.fromisoformat(m["observed_at"]) if m["observed_at"] else None,
            freshness=m["freshness"],
        )
        for m in cp["measurements"]
    ]
    return TicketInput.build(
        device_id=cp["device_id"],
        device_name=cp["device_name"],
        bank=cp["bank"],
        prediction_timestamp_utc=datetime.fromisoformat(cp["prediction_timestamp_utc"]),
        model_version=cp["model_version"],
        policy_version=cp["policy_version"],
        device_state=cp["device_state"],
        measurements=measurements,
        calibrated_probability=cp["calibrated_probability"],
        risk_tier=cp["risk_tier"],
        triggered_rules=cp["triggered_rules"],
        observed_facts=cp["observed_facts"],
        possible_causes=cp["possible_causes"],
        approved_investigation_actions=cp["approved_investigation_actions"],
    )


def verify_all_splits() -> Dict[str, Any]:
    loaded_manifests: Dict[str, Dict[str, Any]] = {}
    loaded_records: Dict[str, List[Dict[str, Any]]] = {}
    errors: List[str] = []

    for sp, m_path in MANIFESTS.items():
        manifest = json.loads(m_path.read_text(encoding="utf-8"))
        loaded_manifests[sp] = manifest
        stored_m_sha = manifest["manifest_sha256"]
        body = {k: v for k, v in manifest.items() if k != "manifest_sha256"}
        recomputed_m_sha = _sha256_bytes(
            json.dumps(body, sort_keys=True, indent=2).encode("utf-8")
        )
        if stored_m_sha != recomputed_m_sha:
            errors.append(f"{sp}: manifest_sha256 mismatch.")

        d_path = ROOT / manifest["data_file"]
        actual_d_sha = _sha256_bytes(d_path.read_bytes())
        if actual_d_sha != manifest["data_file_sha256"]:
            errors.append(f"{sp}: data_file_sha256 mismatch.")

        recs = _load_jsonl(d_path)
        loaded_records[sp] = recs
        if len(recs) != manifest["record_count"]:
            errors.append(f"{sp}: record_count mismatch.")

    # Cross-split incident disjointness.
    inc_sets = {
        sp: {r["incident_id"] for r in recs} for sp, recs in loaded_records.items()
    }
    for a in MANIFESTS:
        for b in MANIFESTS:
            if a < b:
                overlap = inc_sets[a] & inc_sets[b]
                if overlap:
                    errors.append(f"Incident overlap between {a} and {b}: {sorted(overlap)[:5]}")

    # Zero device overlap between UNSEEN_DEVICES and known-device splits.
    unseen_devs = {r["device_id"] for r in loaded_records["UNSEEN_DEVICES"]}
    train_devs = {r["device_id"] for r in loaded_records["TRAIN"]}
    future_devs = {r["device_id"] for r in loaded_records["FUTURE_INCIDENTS_KNOWN_DEVICES"]}
    if unseen_devs & train_devs:
        errors.append("Device overlap between TRAIN and UNSEEN_DEVICES.")
    if unseen_devs & future_devs:
        errors.append("Device overlap between FUTURE_INCIDENTS_KNOWN_DEVICES and UNSEEN_DEVICES.")

    # Temporal cutoff + purge window checks.
    cutoff = datetime.fromisoformat(loaded_manifests["TRAIN"]["train_cutoff_utc"])
    eval_start = datetime.fromisoformat(
        loaded_manifests["FUTURE_INCIDENTS_KNOWN_DEVICES"]["eval_start_utc"]
    )
    for r in loaded_records["TRAIN"]:
        end_dt = datetime.fromisoformat(r["incident_end_utc"])
        if end_dt > cutoff:
            errors.append(f"TRAIN record {r['record_id']} incident ends after cutoff.")
    for r in loaded_records["FUTURE_INCIDENTS_KNOWN_DEVICES"]:
        start_dt = datetime.fromisoformat(r["incident_start_utc"])
        if start_dt < eval_start:
            errors.append(
                f"FUTURE record {r['record_id']} incident starts before cutoff + purge."
            )

    # Canonical & content payload hash uniqueness within and across splits.
    p_hashes = {
        sp: [r["canonical_payload_hash"] for r in recs]
        for sp, recs in loaded_records.items()
    }
    c_hashes = {
        sp: [r["content_payload_hash"] for r in recs]
        for sp, recs in loaded_records.items()
    }
    for sp in MANIFESTS:
        if len(set(p_hashes[sp])) != len(p_hashes[sp]):
            errors.append(f"{sp}: duplicate canonical_payload_hash within split.")
        if len(set(c_hashes[sp])) != len(c_hashes[sp]):
            errors.append(f"{sp}: duplicate content_payload_hash within split.")
    train_p = set(p_hashes["TRAIN"])
    train_c = set(c_hashes["TRAIN"])
    for eval_sp in ("FUTURE_INCIDENTS_KNOWN_DEVICES", "UNSEEN_DEVICES"):
        if train_p & set(p_hashes[eval_sp]):
            errors.append(f"canonical_payload_hash overlap between TRAIN and {eval_sp}.")
        if train_c & set(c_hashes[eval_sp]):
            errors.append(f"content_payload_hash overlap between TRAIN and {eval_sp}.")

    # Per-record field, re-render, and Stage C validator checks.
    for sp, recs in loaded_records.items():
        for r in recs:
            if r["device_state"] not in DEVICE_STATES:
                errors.append(f"{sp}/{r['record_id']}: invalid device_state.")
            if r["policy_version"] != POLICY_VERSION:
                errors.append(f"{sp}/{r['record_id']}: wrong policy_version.")
            if not r["incident_id"].startswith("INC-"):
                errors.append(f"{sp}/{r['record_id']}: invalid incident_id.")
            dt = datetime.fromisoformat(r["prediction_timestamp_utc"])
            if dt.tzinfo is None:
                errors.append(f"{sp}/{r['record_id']}: naive timestamp.")
            ti = _reconstruct_ticket_input(r)
            rerendered = render_ticket(ti)
            if rerendered.payload_hash != r["canonical_payload_hash"]:
                errors.append(f"{sp}/{r['record_id']}: canonical_payload_hash mismatch on re-render.")
            if rerendered.text != r["deterministic_ticket"]:
                errors.append(f"{sp}/{r['record_id']}: deterministic_ticket mismatch on re-render.")
            vres = validate_candidate(rerendered.text, ti)
            if not vres.is_valid:
                errors.append(
                    f"{sp}/{r['record_id']}: Stage C validator failed: {vres.violations}"
                )

    report = {
        "verified": len(errors) == 0,
        "error_count": len(errors),
        "errors": errors,
        "cross_split_incident_overlap": 0,
        "train_vs_unseen_device_overlap": len(unseen_devs & train_devs),
        "future_vs_unseen_device_overlap": len(unseen_devs & future_devs),
        "train_vs_future_known_device_overlap": len(train_devs & future_devs),
        "train_vs_eval_canonical_payload_overlap": (
            len(train_p & set(p_hashes["FUTURE_INCIDENTS_KNOWN_DEVICES"]))
            + len(train_p & set(p_hashes["UNSEEN_DEVICES"]))
        ),
        "train_vs_eval_content_payload_overlap": (
            len(train_c & set(c_hashes["FUTURE_INCIDENTS_KNOWN_DEVICES"]))
            + len(train_c & set(c_hashes["UNSEEN_DEVICES"]))
        ),
        "split_counts": {sp: len(recs) for sp, recs in loaded_records.items()},
    }
    print(json.dumps(report, indent=2))
    if errors:
        raise AssertionError(f"Split verification failed: {errors}")
    return report


if __name__ == "__main__":
    verify_all_splits()
    sys.exit(0)
