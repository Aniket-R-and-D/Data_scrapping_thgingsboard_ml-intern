"""tests/test_stage_e_comparison.py — Stage E LLM value evaluation & review-pack tests.

Verifies:
1. Raw evaluation artifact exists, uses ONLY the frozen Stage D evaluation sets
   (FUTURE_INCIDENTS_KNOWN_DEVICES: 407, UNSEEN_DEVICES: 150), and records
   exact model versions, checkpoint SHA-256 hashes, prompt SHA-256, generation
   settings, latencies, and GPU memory.
2. Per-set results are reported separately (never pooled) and record zero
   payload duplication relative to TRAIN.
3. Paired comparisons (deterministic vs base, deterministic vs remediated Qwen,
   base vs remediated Qwen) are present for both splits and show that neither
   LLM adds supported information absent from the deterministic renderer.
4. Independent semantic accuracy is explicitly marked 'NOT MEASURED'.
5. The 30-case human review pack (10 FUTURE_INCIDENTS_KNOWN_DEVICES,
   10 UNSEEN_DEVICES, 10 difficult) has randomized candidate ordering and
   100% blank (null) reviewer score fields, with status 'HUMAN REVIEW PENDING'
   and cohens_kappa = None.
6. The experimental LLM feature flag remains default-OFF.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.tickets.llm_bridge import is_experimental_llm_enabled
from src.tickets.policy import POLICY_VERSION

ROOT = Path(__file__).resolve().parents[1]
RAW_RESULTS_FILE = ROOT / "reports" / "phase_r5_stage_e_raw_results.json"
REVIEW_PACK_DIR = ROOT / "reports" / "phase_r5_stage_e_human_review_pack"
FORMS_FILE = REVIEW_PACK_DIR / "blank_review_forms_30_cases.json"
KEY_FILE = REVIEW_PACK_DIR / "blinding_key_DO_NOT_OPEN_DURING_REVIEW.json"


@pytest.fixture(scope="module")
def stage_e_results():
    assert RAW_RESULTS_FILE.exists(), "Stage E raw results JSON missing."
    return json.loads(RAW_RESULTS_FILE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def review_pack():
    assert FORMS_FILE.exists(), "Human review pack forms JSON missing."
    assert KEY_FILE.exists(), "Human review pack blinding key JSON missing."
    forms = json.loads(FORMS_FILE.read_text(encoding="utf-8"))
    key = json.loads(KEY_FILE.read_text(encoding="utf-8"))
    return forms, key


def test_only_clean_stage_d_evaluation_splits_used(stage_e_results):
    assert set(stage_e_results["per_set_results"].keys()) == {
        "FUTURE_INCIDENTS_KNOWN_DEVICES",
        "UNSEEN_DEVICES",
    }
    assert set(stage_e_results["cases"].keys()) == {
        "FUTURE_INCIDENTS_KNOWN_DEVICES",
        "UNSEEN_DEVICES",
    }
    assert len(stage_e_results["cases"]["FUTURE_INCIDENTS_KNOWN_DEVICES"]) == 407
    assert len(stage_e_results["cases"]["UNSEEN_DEVICES"]) == 150


def test_model_hashes_and_identical_generation_settings_recorded(stage_e_results):
    assert stage_e_results["policy_version"] == POLICY_VERSION
    gs = stage_e_results["generation_settings"]
    assert gs["max_new_tokens"] == 220
    assert gs["do_sample"] is False
    assert gs["repetition_penalty"] == 1.1
    assert len(stage_e_results["system_prompt_sha256"]) == 64

    models = stage_e_results["models"]
    assert len(models["base_qwen25_05b_instruct"]["weights_sha256"]) == 64
    assert len(models["remediated_qwen25_05b_lora"]["merged_weights_sha256"]) == 64
    assert len(models["remediated_qwen25_05b_lora"]["lora_adapter_sha256"]) == 64
    assert models["base_qwen25_05b_instruct"]["peak_gpu_memory_mb"] > 0
    assert models["remediated_qwen25_05b_lora"]["peak_gpu_memory_mb"] > 0


def test_per_set_metrics_and_paired_comparisons_complete(stage_e_results):
    for sp_name, expected_n in (
        ("FUTURE_INCIDENTS_KNOWN_DEVICES", 407),
        ("UNSEEN_DEVICES", 150),
    ):
        sp = stage_e_results["per_set_results"][sp_name]
        for sys_name in (
            "deterministic_renderer",
            "base_qwen25_05b_instruct",
            "remediated_qwen25_05b_lora",
        ):
            s = sp["systems"][sys_name]
            assert s["record_count"] == expected_n
            assert s["exact_payload_duplication_vs_train"] == 0
            assert s["independent_semantic_accuracy"] == "NOT MEASURED"
            assert "mean" in s["latency_seconds"] and "p95" in s["latency_seconds"]
            assert set(s["commentary_usefulness_counts"].keys()) == {
                "adds_supported_investigation_step",
                "adds_supported_observed_fact",
                "adds_no_new_information",
                "adds_unsupported_claim",
            }
        # Deterministic renderer achieves 100% Stage C acceptance & 0 unsupported claims.
        det = sp["systems"]["deterministic_renderer"]
        assert det["stage_c_field_validator_acceptance_rate"] == 1.0
        assert det["records_with_unsupported_claims_count"] == 0

        # Neither LLM adds supported info absent from the deterministic ticket.
        pc = sp["paired_comparisons"]
        assert (
            pc["deterministic_vs_base"][
                "base_qwen25_05b_instruct_adds_supported_info_absent_from_deterministic"
            ]
            == 0
        )
        assert (
            pc["deterministic_vs_remediated_qwen"][
                "remediated_qwen25_05b_lora_adds_supported_info_absent_from_deterministic"
            ]
            == 0
        )


def test_human_review_pack_stratified_randomized_and_blank(review_pack):
    forms, key = review_pack
    assert forms["human_review_status"] == "HUMAN REVIEW PENDING"
    assert forms["completed_reviews_count"] == 0
    assert forms["cohens_kappa"] is None
    assert len(forms["cases"]) == 30
    assert len(key["blinding_key"]) == 30

    s1 = sum(1 for c in forms["cases"] if c["stratum"].startswith("STRATUM_1_"))
    s2 = sum(1 for c in forms["cases"] if c["stratum"].startswith("STRATUM_2_"))
    s3 = sum(1 for c in forms["cases"] if c["stratum"].startswith("STRATUM_3_"))
    assert (s1, s2, s3) == (10, 10, 10)

    orders_seen = set()
    for c, k in zip(forms["cases"], key["blinding_key"]):
        assert c["record_id"] == k["record_id"]
        assert set(c["randomized_candidates"].keys()) == {"Option_A", "Option_B", "Option_C"}
        mapping = k["option_to_system"]
        assert set(mapping.values()) == {
            "deterministic_renderer",
            "base_qwen25_05b_instruct",
            "remediated_qwen25_05b_lora",
        }
        orders_seen.add((mapping["Option_A"], mapping["Option_B"], mapping["Option_C"]))

        sf = c["scoring_form"]
        assert sf["reviewer_id"] is None
        assert sf["review_timestamp_utc"] is None
        assert sf["preferred_ticket_to_send"] is None
        for opt in ("Option_A", "Option_B", "Option_C"):
            assert sf[opt]["factual_consistency_1_to_5"] is None
            assert sf[opt]["claim_grounding_pass_or_fail"] is None
            assert sf[opt]["operational_actionability_1_to_5"] is None
            assert sf[opt]["harmful_or_misleading_recommendation_yes_or_no"] is None

    # Presentation order must actually be randomized across the 30 cases.
    assert len(orders_seen) >= 4


def test_llm_remains_disabled_by_default():
    assert is_experimental_llm_enabled() is False
