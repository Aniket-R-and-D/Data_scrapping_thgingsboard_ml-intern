"""verify_r5_evidence.py — Stage A evidence reproduction for the R5 correction notice.

Reproduces, from preserved artifacts only (no model inference, no retraining):

  1. Split-integrity statistics for the R5 explanation-layer train/test JSONL files
     (record counts, internal duplicates, cross-split exact payload/completion
     duplication, device overlap, SHA-256 checksums).
  2. Cross-split incident-overlap evidence: test records that share device, device
     state, risk tier, and near-identical ongoing-incident telemetry with training
     records (adjacent snapshots of the same physical event), plus candidate
     timestamp-window overlap reconstructed from the source shadow log + feature
     matrix that the dataset generator sampled from.
  3. Raw validator pass rates from the preserved 100-case evaluation export
     (legacy old/fixed validator, remediated old/fixed validator, fallback count).
  4. The deterministic score pattern of the Phase 6 "human_reviewed_sample_30"
     (proving the scores are a hardcoded function of `fallback_used`).
  5. Static confirmation that the R5 "two-reviewer audit" scores originate from
     hardcoded conditional branches in src/explain/audit_evaluator.py, and that
     the stored Cohen's kappa equals a re-computation from the stored per-case
     scores (i.e., a property of the simulation, not of human raters).

Output: reports/phase_r5_stage_a_reproduction.json + console summary.
Exit code 0 on success, 1 on any internal error.

This script is READ-ONLY with respect to historical evidence: it never modifies
checkpoints, datasets, or prior reports.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
REPORTS = ROOT / "reports"
TRAIN_FILE = ROOT / "training_data" / "remediated_explanation_train.jsonl"
TEST_FILE = ROOT / "training_data" / "remediated_explanation_test_heldout.jsonl"
RAW_EVAL_FILE = REPORTS / "phase_r5_raw_evaluation_100_cases.json"
PHASE6_EVAL_FILE = REPORTS / "phase_6_explanation_evaluation.json"
HUMAN_AUDIT_FILE = REPORTS / "human_audit_report.json"
AUDIT_EVALUATOR_SRC = ROOT / "src" / "explain" / "audit_evaluator.py"
SHADOW_LOG = ROOT / "data" / "processed" / "remediated_shadow_predictions.jsonl"
FEATURES_PARQUET = ROOT / "data" / "processed" / "remediated_features_matrix.parquet"
OUT_FILE = REPORTS / "phase_r5_stage_a_reproduction.json"


def load_jsonl(path: Path) -> list[dict]:
    records = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                records.append(json.loads(line))
    return records


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def canonical_payload(record: dict) -> str:
    return json.dumps(record.get("payload", {}), sort_keys=True, ensure_ascii=False)


def canonical_completion(record: dict) -> str:
    msgs = record.get("messages", [])
    return msgs[-1]["content"] if msgs else ""


def verify_split_integrity() -> dict:
    train = load_jsonl(TRAIN_FILE)
    test = load_jsonl(TEST_FILE)

    train_payloads = [canonical_payload(r) for r in train]
    test_payloads = [canonical_payload(r) for r in test]
    train_completions = [canonical_completion(r) for r in train]
    test_completions = [canonical_completion(r) for r in test]

    train_payload_set = set(train_payloads)
    test_payload_set = set(test_payloads)
    train_completion_set = set(train_completions)
    test_completion_set = set(test_completions)

    train_devices = {r["device_id"] for r in train}
    test_devices = {r["device_id"] for r in test}

    exact_dup_test_in_train = [i + 1 for i, p in enumerate(test_payloads) if p in train_payload_set]
    completion_dup_test_in_train = [
        i + 1 for i, c in enumerate(test_completions) if c in train_completion_set
    ]
    distinct_dup_payload_values = len({p for p in test_payloads if p in train_payload_set})
    distinct_dup_completion_values = len({c for c in test_completions if c in train_completion_set})

    return {
        "train_records": len(train),
        "test_records": len(test),
        "train_unique_payloads": len(train_payload_set),
        "test_unique_payloads": len(test_payload_set),
        "train_internal_duplicate_rows": len(train) - len(train_payload_set),
        "test_internal_duplicate_rows": len(test) - len(test_payload_set),
        "test_records_with_payload_exactly_in_train": len(exact_dup_test_in_train),
        "distinct_test_payload_values_also_in_train": distinct_dup_payload_values,
        "test_payload_dup_case_ids": exact_dup_test_in_train,
        "test_records_with_completion_exactly_in_train": len(completion_dup_test_in_train),
        "distinct_test_completion_values_also_in_train": distinct_dup_completion_values,
        "test_completion_dup_case_ids": completion_dup_test_in_train,
        "test_unique_devices": len(test_devices),
        "test_devices_also_in_train": len(test_devices & train_devices),
        "test_devices_only_in_test": sorted(test_devices - train_devices),
        "sha256_train_file": sha256_file(TRAIN_FILE),
        "sha256_test_file": sha256_file(TEST_FILE),
    }


def verify_incident_overlap() -> dict:
    """Show test records that are adjacent snapshots of the same ongoing incident
    as training records (same device, same device_state, same risk_tier, and
    near-identical ongoing-incident telemetry), plus candidate-timestamp overlap
    reconstructed from the source pool the generator sampled from."""
    train = load_jsonl(TRAIN_FILE)
    test = load_jsonl(TEST_FILE)

    # ---- Timestamp-free same-incident adjacency -------------------------------
    # Group training records by (device_id, device_state, risk_tier).
    train_groups: dict[tuple, list[dict]] = defaultdict(list)
    for r in train:
        p = r["payload"]
        key = (p["device_id"], p["device_state"], p["risk_tier"])
        train_groups[key].append(p)

    same_incident_neighbor_cases = []
    for i, r in enumerate(test):
        p = r["payload"]
        key = (p["device_id"], p["device_state"], p["risk_tier"])
        cands = train_groups.get(key, [])
        for tp in cands:
            same_ac = p["telemetry_snapshot"]["ac_voltage"] == tp["telemetry_snapshot"]["ac_voltage"]
            m1 = p["telemetry_snapshot"]["time_since_mains_loss_hours"]
            m2 = tp["telemetry_snapshot"]["time_since_mains_loss_hours"]
            mains_close = abs(float(m1) - float(m2)) <= 2.0
            if same_ac and mains_close:
                same_incident_neighbor_cases.append(
                    {
                        "test_case_id": i + 1,
                        "device_name": p["device_name"],
                        "device_state": p["device_state"],
                        "risk_tier": p["risk_tier"],
                        "test_mains_loss_h": m1,
                        "train_mains_loss_h": m2,
                        "test_ac_voltage": p["telemetry_snapshot"]["ac_voltage"],
                    }
                )
                break

    def is_active_incident(p: dict) -> bool:
        return (
            p["telemetry_snapshot"]["ac_voltage"] == 0.0
            or p["device_state"] == "ONLINE_POWER_LOSS"
            or p["risk_tier"] == "CONNECTIVITY_ISSUE"
        )

    active_ids = {c["test_case_id"] for c in same_incident_neighbor_cases}
    active_total = sum(1 for r in test if is_active_incident(r["payload"]))
    active_with_neighbor = sum(
        1
        for i, r in enumerate(test)
        if is_active_incident(r["payload"]) and (i + 1) in active_ids
    )

    # ---- Candidate timestamp-window overlap (distinctive rows) ---------------
    preds = []
    with open(SHADOW_LOG, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                preds.append(json.loads(line))
    dfp = pd.DataFrame(preds)
    dfp["ts_str"] = dfp["timestamp"].astype(str)
    feats = pd.read_parquet(
        FEATURES_PARQUET,
        columns=[
            "device_id",
            "timestamp",
            "is_battery_sensor_present",
            "ac_voltage_mean_1h",
            "battery_voltage_mean_1h",
            "time_since_mains_loss_hours",
        ],
    )
    feats["ts_str"] = feats["timestamp"].astype(str)
    dfm = dfp.merge(feats, on=["device_id", "ts_str"], how="inner")

    def ac_disp(v):
        return round(float(v), 1) if pd.notna(v) else "N/A"

    def batt_disp(is_batt, v):
        if is_batt and pd.notna(v) and float(v) > 0.0:
            return round(float(v), 2)
        return "N/A"

    dfm["ac_d"] = dfm["ac_voltage_mean_1h"].map(ac_disp)
    dfm["batt_d"] = [
        batt_disp(bool(b), v)
        for b, v in zip(dfm["is_battery_sensor_present"], dfm["battery_voltage_mean_1h"])
    ]
    dfm["mains_d"] = dfm["time_since_mains_loss_hours"].map(
        lambda v: round(float(v), 1) if pd.notna(v) else 0.0
    )
    dfm["prob_d"] = dfm["calibrated_prob"].map(lambda v: round(float(v), 4))
    dfm["rules_key"] = dfm["rules_triggered"].map(lambda r: tuple(sorted(r)))

    lookup: dict[tuple, list[datetime]] = defaultdict(list)
    for ts, dev, state, tier, prob, rk, ac, bd, md in zip(
        dfm["ts_str"], dfm["device_id"], dfm["device_state"], dfm["risk_tier"],
        dfm["prob_d"], dfm["rules_key"], dfm["ac_d"], dfm["batt_d"], dfm["mains_d"],
    ):
        lookup[(dev, state, tier, prob, rk, ac, bd, md)].append(
            datetime.fromisoformat(ts)
        )

    def candidates(rec: dict) -> list[datetime]:
        p = rec["payload"]
        s = p["telemetry_snapshot"]
        key = (
            p["device_id"], p["device_state"], p["risk_tier"],
            round(float(p["calibrated_prob"]), 4), tuple(sorted(p["rules_triggered"])),
            s["ac_voltage"], s["battery_voltage"], s["time_since_mains_loss_hours"],
        )
        return lookup.get(key, [])

    # Distinctive test rows = payload maps to <= 5 source rows (near-unique rows).
    test_cands = {i + 1: candidates(r) for i, r in enumerate(test)}
    train_cands = [(r["payload"], candidates(r)) for r in train]

    distinctive_overlap_24h = 0
    distinctive_total = 0
    distinctive_examples = []
    for case_id, cands in test_cands.items():
        if not cands or len(cands) > 5:
            continue
        distinctive_total += 1
        p = test[case_id - 1]["payload"]
        best_gap = None
        for tp, tc in train_cands:
            if tp["device_id"] != p["device_id"] or tp["device_state"] != p["device_state"]:
                continue
            for t1 in cands:
                for t2 in tc:
                    gap = abs((t1 - t2).total_seconds())
                    if best_gap is None or gap < best_gap:
                        best_gap = gap
        if best_gap is not None and best_gap <= 24 * 3600:
            distinctive_overlap_24h += 1
            if len(distinctive_examples) < 5:
                distinctive_examples.append(
                    {
                        "test_case_id": case_id,
                        "device_name": p["device_name"],
                        "device_state": p["device_state"],
                        "risk_tier": p["risk_tier"],
                        "min_candidate_gap_hours": round(best_gap / 3600.0, 2),
                    }
                )

    return {
        "test_records_with_same_incident_train_neighbor": len(same_incident_neighbor_cases),
        "active_incident_test_rows": active_total,
        "active_incident_rows_with_same_condition_train_neighbor": active_with_neighbor,
        "same_incident_neighbor_examples": same_incident_neighbor_cases[:10],
        "note_same_incident_neighbor": (
            "Test records sharing device_id + device_state + risk_tier + identical "
            "AC voltage + mains-loss duration within 2.0h with at least one training "
            "record: adjacent snapshots of the same ongoing physical event on both "
            "sides of the split (timestamp-free evidence)."
        ),
        "test_distinctive_rows": distinctive_total,
        "distinctive_rows_with_train_candidate_within_24h": distinctive_overlap_24h,
        "distinctive_overlap_examples": distinctive_examples,
        "note_distinctive_rows": (
            "Distinctive rows are test payloads that map to <= 5 source shadow-log "
            "rows (near-unique telemetry). Of these, the count whose candidate "
            "timestamp window has a same-device, same-state training record within "
            "24 hours, reconstructed from remediated_shadow_predictions.jsonl + "
            "remediated_features_matrix.parquet."
        ),
        "structural_note": (
            "src/explain/generate_explanation_dataset.py sampled rows uniformly at "
            "random (row-level, seed 42) from one 90-day backtest pool and split "
            "positionally (all[:-100] vs last 100) with no device, incident, or "
            "temporal grouping; the payloads themselves carry no timestamp."
        ),
    }


def verify_raw_eval_pass_rates() -> dict:
    with open(RAW_EVAL_FILE, encoding="utf-8") as f:
        d = json.load(f)
    cases = d["cases"]

    def is_pass(v: dict) -> bool:
        return isinstance(v, dict) and v.get("pass") is True

    recompute = {
        "legacy_old_validator_pass": sum(is_pass(c["legacy_old_validator"]) for c in cases),
        "legacy_fixed_validator_pass": sum(is_pass(c["legacy_fixed_validator"]) for c in cases),
        "remediated_old_validator_pass": sum(is_pass(c["remediated_old_validator"]) for c in cases),
        "remediated_fixed_validator_pass": sum(is_pass(c["remediated_fixed_validator"]) for c in cases),
        "fallback_used_count": sum(1 for c in cases if c["fallback_used"]),
        "total_cases": len(cases),
    }
    stored = d["metadata"]["summary_metrics"]

    sole_failures = [
        {"case_id": c["case_id"], "device_name": c["device_name"],
         "violations": c["remediated_fixed_validator"]["violations"]}
        for c in cases if not is_pass(c["remediated_fixed_validator"])
    ]

    # A concrete false-acceptance example: legacy case 2 (BOI-DOBSON) passed the
    # fixed validator while making semantically unsupported claims.
    false_accept_example = None
    for c in cases:
        if c["case_id"] == 2:
            false_accept_example = {
                "case_id": c["case_id"],
                "device_name": c["device_name"],
                "fixed_validator_decision": c["legacy_fixed_validator"],
                "raw_output_excerpt": c["legacy_raw_output"][:300],
            }

    return {
        "recomputed_pass_counts": recompute,
        "stored_metadata_summary_metrics": stored,
        "matches_stored_metadata": (
            recompute["legacy_old_validator_pass"] == round(stored["legacy_raw_pass_rate_old_validator"] * 100)
            and recompute["legacy_fixed_validator_pass"] == round(stored["legacy_raw_pass_rate_fixed_validator"] * 100)
            and recompute["remediated_old_validator_pass"] == round(stored["remediated_raw_pass_rate_old_validator"] * 100)
            and recompute["remediated_fixed_validator_pass"] == round(stored["remediated_raw_pass_rate_fixed_validator"] * 100)
        ),
        "remediated_sole_failure_cases": sole_failures,
        "legacy_false_acceptance_example_case_2": false_accept_example,
    }


def verify_phase6_score_pattern() -> dict:
    with open(PHASE6_EVAL_FILE, encoding="utf-8") as f:
        d = json.load(f)
    cases = d["human_reviewed_sample_30"]
    pattern = Counter(
        (c["fallback_used"], c["factual_consistency_score_out_of_5"], c["actionability_score_out_of_5"])
        for c in cases
    )
    return {
        "n_cases": len(cases),
        "pattern_counts": [
            {
                "fallback_used": k[0],
                "factual_score": k[1],
                "actionability_score": k[2],
                "count": v,
            }
            for k, v in sorted(pattern.items(), key=lambda x: (x[0][0], x[0][1]))
        ],
        "distinct_score_pairs": len(pattern),
        "conclusion": (
            "Every one of the 30 'human reviewed' scores is one of exactly two "
            "combustions determined solely by fallback_used (True -> factual 4 / "
            "actionability 5; False -> factual 5 / actionability 4). No generator "
            "script for these scores exists anywhere in the repository; the pattern "
            "is a hardcoded rule, not human review."
        ),
    }


def verify_audit_evaluator_hardcoding() -> dict:
    src = AUDIT_EVALUATOR_SRC.read_text(encoding="utf-8")
    reviewer1_branch = "if is_faithful and not fallback_used:" in src
    reviewer2_branch = "if is_faithful:" in src and 's2_a = 5 if "DISPATCH" in text or "Investigate" in text else 4' in src
    simulation_comment = "Independent review simulation" in src

    with open(HUMAN_AUDIT_FILE, encoding="utf-8") as f:
        audit = json.load(f)
    r1 = [c["reviewer_1"]["factual_score"] for c in audit["audited_cases_sample"]]
    r2 = [c["reviewer_2"]["factual_score"] for c in audit["audited_cases_sample"]]

    def cohens_kappa(a, b):
        n = len(a)
        cats = sorted(set(a) | set(b))
        idx = {c: i for i, c in enumerate(cats)}
        k = len(cats)
        mat = [[0] * k for _ in range(k)]
        for x, y in zip(a, b):
            mat[idx[x]][idx[y]] += 1
        p_o = sum(mat[i][i] for i in range(k)) / n
        rows = [sum(mat[i]) / n for i in range(k)]
        cols = [sum(mat[i][j] for i in range(k)) / n for j in range(k)]
        p_e = sum(rows[i] * cols[i] for i in range(k))
        return round((p_o - p_e) / (1 - p_e), 4) if (1 - p_e) else 1.0

    stored_kappa = audit["inter_rater_agreement"]["cohens_kappa_factual_consistency"]
    recomputed_kappa = cohens_kappa(r1, r2)

    return {
        "audit_evaluator_hardcoded_reviewer1_branch_present": reviewer1_branch,
        "audit_evaluator_hardcoded_reviewer2_branch_present": reviewer2_branch,
        "source_admits_simulation": simulation_comment,
        "stored_kappa": stored_kappa,
        "recomputed_kappa_from_stored_case_scores": recomputed_kappa,
        "kappa_consistent_with_stored": abs(stored_kappa - recomputed_kappa) < 1e-9,
        "conclusion": (
            "The 'two independent specialist reviewers' of Phase R5 Section 4 are "
            "hardcoded if/else branches in src/explain/audit_evaluator.py. The stored "
            "Cohen's kappa reproduces exactly from the stored per-case scores, "
            "confirming the inter-rater statistic is a property of the simulation."
        ),
    }


def main() -> int:
    print("=" * 78)
    print("Stage A evidence reproduction — R5 correction record")
    print("=" * 78)

    results = {
        "generated_by": "verify_r5_evidence.py (Stage A, R5 reliability recovery)",
        "scope": (
            "Read-only reproduction from preserved artifacts. No model inference, "
            "no retraining, no modification of historical evidence."
        ),
        "split_integrity": verify_split_integrity(),
    }
    print("\n[1/5] Split integrity ... OK")
    si = results["split_integrity"]
    print(
        f"      train={si['train_records']} test={si['test_records']} | "
        f"test records w/ payload exactly in train: {si['test_records_with_payload_exactly_in_train']}/100 "
        f"({si['distinct_test_payload_values_also_in_train']} distinct values) | "
        f"test records w/ completion exactly in train: {si['test_records_with_completion_exactly_in_train']}/100 "
        f"({si['distinct_test_completion_values_also_in_train']} distinct values) | "
        f"devices cross-split: {si['test_devices_also_in_train']}/{si['test_unique_devices']}"
    )

    print("\n[2/5] Incident overlap (may take ~1 min) ...")
    results["incident_overlap"] = verify_incident_overlap()
    io = results["incident_overlap"]
    print(
        f"      same-incident train neighbors for test records: "
        f"{io['test_records_with_same_incident_train_neighbor']}/{si['test_records']} | "
        f"active-incident rows w/ same-condition train neighbor: "
        f"{io['active_incident_rows_with_same_condition_train_neighbor']}/{io['active_incident_test_rows']} | "
        f"distinctive rows w/ train candidate within 24h: "
        f"{io['distinctive_rows_with_train_candidate_within_24h']}/{io['test_distinctive_rows']}"
    )

    results["raw_eval_pass_rates"] = verify_raw_eval_pass_rates()
    print("\n[3/5] Raw evaluation pass rates ... OK")
    rp = results["raw_eval_pass_rates"]["recomputed_pass_counts"]
    print(
        f"      legacy old/fixed: {rp['legacy_old_validator_pass']}/{rp['legacy_fixed_validator_pass']} | "
        f"remediated old/fixed: {rp['remediated_old_validator_pass']}/{rp['remediated_fixed_validator_pass']} | "
        f"fallbacks: {rp['fallback_used_count']} | matches stored metadata: "
        f"{results['raw_eval_pass_rates']['matches_stored_metadata']}"
    )

    results["phase6_score_pattern"] = verify_phase6_score_pattern()
    print("\n[4/5] Phase 6 'human review' score pattern ... OK")
    pp = results["phase6_score_pattern"]
    print(
        f"      distinct (factual, actionability) pairs across 30 cases: "
        f"{pp['distinct_score_pairs']} -> {pp['pattern_counts']}"
    )

    results["audit_evaluator_hardcoding"] = verify_audit_evaluator_hardcoding()
    print("\n[5/5] R5 audit evaluator hardcoding ... OK")
    ae = results["audit_evaluator_hardcoding"]
    print(
        f"      hardcoded reviewer branches present: "
        f"{ae['audit_evaluator_hardcoded_reviewer1_branch_present'] and ae['audit_evaluator_hardcoded_reviewer2_branch_present']} | "
        f"stored kappa {ae['stored_kappa']} == recomputed {ae['recomputed_kappa_from_stored_case_scores']}"
    )

    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
    print(f"\nWrote {OUT_FILE}")
    print("Stage A reproduction complete. Exit code 0.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
