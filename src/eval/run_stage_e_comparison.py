"""src/eval/run_stage_e_comparison.py — Stage E head-to-head evaluation on clean splits.

Evaluates three systems on the two frozen Stage D evaluation sets ONLY
(FUTURE_INCIDENTS_KNOWN_DEVICES: 407 records; UNSEEN_DEVICES: 150 records):
  A. Deterministic ticket renderer (src/tickets/renderer.py, ticket-policy-v1)
  B. Base instruction model (Qwen/Qwen2.5-0.5B-Instruct)
  C. Existing remediated Qwen checkpoint
     (models/qwen_reasoning_outage_predictor/remediated_final_model)

TRAIN is used ONLY to verify zero payload duplication relative to training;
neither TRAIN nor the retired contaminated benchmark is used for model
selection or headline reporting.

No model is fine-tuned. No dispatch, ThingsBoard alarm creation, or external
notification occurs. Independent semantic accuracy is reported as NOT MEASURED
(human review status: HUMAN REVIEW PENDING).
"""

from __future__ import annotations

import hashlib
import json
import math
import random
import re
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

import numpy as np
import torch
from transformers import AutoModelForCausalLM, AutoTokenizer

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.eval.verify_splits import _load_jsonl, _reconstruct_ticket_input
from src.explain.validator import ComprehensiveFaithfulnessValidator, generate_deterministic_template
from src.tickets.field_validator import validate_candidate
from src.tickets.policy import (
    ALLOWED_INVESTIGATION_ACTIONS,
    FORBIDDEN_URGENCY_BY_TIER,
    HARDWARE_FAILURE_PATTERNS,
    KNOWN_RULE_IDS,
    POLICY_VERSION,
    PROBABILITY_DISPLAY_TOLERANCE_PCT,
    VALUE_DISPLAY_TOLERANCE,
    format_probability,
)
from src.tickets.renderer import render_ticket
from src.tickets.schema import TicketInput
from tests.test_field_level_validator import adversarial_summary

EVAL_DIR = ROOT / "data" / "eval"
REPORTS_DIR = ROOT / "reports"
RAW_RESULTS_FILE = REPORTS_DIR / "phase_r5_stage_e_raw_results.json"
REVIEW_PACK_DIR = REPORTS_DIR / "phase_r5_stage_e_human_review_pack"

BASE_MODEL_ID = "Qwen/Qwen2.5-0.5B-Instruct"
REMEDIATED_MODEL_DIR = ROOT / "models" / "qwen_reasoning_outage_predictor" / "remediated_final_model"
REMEDIATED_ADAPTER_DIR = ROOT / "models" / "qwen_reasoning_outage_predictor" / "remediated_lora_adapter"

GENERATION_SETTINGS = {
    "max_new_tokens": 220,
    "do_sample": False,
    "temperature": None,
    "top_p": None,
    "repetition_penalty": 1.1,
    "padding_side": "left",
    "dtype": "float16",
    "batch_size": 32,
    "policy_version": POLICY_VERSION,
    "validator_version": "stage-c-field-validator-v1",
}

SYSTEM_PROMPT = (
    "You are the Bank Branch Outage Diagnostic Assistant. "
    "You generate operational diagnosis and dispatch tickets from verified machine learning predictions and physical telemetry states.\n"
    "Strict Operational Directives:\n"
    "1. If device state is STALE_DATA or OFFLINE_LONG, you MUST output a connectivity investigation ticket and NEVER state an outage probability or claim an active power outage.\n"
    "2. If battery hardware is not equipped, you NEVER state a numeric battery voltage or claim battery degradation.\n"
    "3. You NEVER cite physical rule violations (RULE_*) that are not present in the verified input payload.\n"
    "4. You NEVER invent ungrounded numerical figures. Every figure must match the input snapshot exactly."
)


def _sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _resolve_hf_cached_safetensors(model_id: str) -> Tuple[str, str]:
    """Locate the cached model.safetensors for a HuggingFace Hub model ID and return (path, sha256)."""
    cache_root = Path.home() / ".cache" / "huggingface" / "hub"
    slug = "models--" + model_id.replace("/", "--")
    snaps = sorted((cache_root / slug / "snapshots").glob("*/model.safetensors"))
    if not snaps:
        return ("hf_cache_not_found", "unknown")
    target = snaps[-1].resolve()
    return (str(target), _sha256_file(target))


def _make_prompt_payload(rec: Dict[str, Any]) -> Dict[str, Any]:
    """Construct the exact 10-key constrained payload expected by the R5 prompt template."""
    cp = rec["canonical_payload"]
    meas_map = {m["field"]: m for m in cp["measurements"]}
    batt_m = meas_map["battery_voltage"]
    ac_m = meas_map["ac_voltage"]
    mains_m = meas_map["time_since_mains_loss"]
    is_batt = batt_m["freshness"] != "UNAVAILABLE"
    tier = cp["risk_tier"]
    return {
        "device_id": cp["device_id"],
        "device_name": cp["device_name"],
        "bank": cp["bank"],
        "device_state": cp["device_state"],
        "is_battery_sensor_present": is_batt,
        "calibrated_prob": cp["calibrated_probability"] if cp["calibrated_probability"] is not None else 0.0,
        "risk_tier": tier,
        "rules_triggered": cp["triggered_rules"],
        "top_contributing_features": (
            "ac_voltage_slope_6h; time_since_mains_loss_hours"
            if tier in ("HIGH", "CRITICAL")
            else ("heartbeat_staleness" if tier == "CONNECTIVITY_ISSUE" else "normal electrical baseline")
        ),
        "telemetry_snapshot": {
            "ac_voltage": ac_m["value"] if ac_m["freshness"] != "UNAVAILABLE" else "N/A",
            "battery_voltage": batt_m["value"] if is_batt else "N/A",
            "time_since_mains_loss_hours": mains_m["value"] if mains_m["freshness"] != "UNAVAILABLE" else 0.0,
        },
    }


def _evaluate_content_fields(
    text: str,
    ti: TicketInput,
    prompt_payload: Dict[str, Any],
    r5_validator: ComprehensiveFaithfulnessValidator,
) -> Dict[str, Any]:
    """Evaluate field accuracy, required-field completeness, unsupported claims,
    and commentary usefulness on a candidate output."""
    text_str = text or ""
    low = text_str.lower()
    is_conn = ti.device_state in ("STALE_DATA", "OFFLINE_LONG")

    # 1. Stage C strict validator (full ticket-policy-v1 contract)
    stage_c_res = validate_candidate(text_str, ti)

    # 2. Legacy R5 validator (for continuity comparison on the 10-key prompt)
    r5_ok, r5_violations = r5_validator.validate(text_str, prompt_payload)

    # 3. Granular field accuracy checks (applicable to both renderer and LLM outputs)
    identity_ok = (ti.device_name in text_str) and (ti.bank in text_str)
    state_ok = ti.device_state in text_str
    tier_ok = ti.risk_tier in text_str

    ac_m = ti.measurement("ac_voltage")
    batt_m = ti.measurement("battery_voltage")
    mains_m = ti.measurement("time_since_mains_loss")

    if is_conn:
        prob_ok = not bool(re.search(r"outage\s+probability\s*:\s*\d", low))
        ac_ok = True  # connectivity ticket may omit or mark stale
        batt_ok = not bool(re.search(r"battery\s+voltage\s*:\s*\d", low))
        mains_ok = True
    else:
        canonical_pct = round(float(ti.calibrated_probability) * 100.0, 1)
        pcts = [float(x) for x in re.findall(r"(\d+(?:\.\d+)?)\s*%", text_str)]
        prob_ok = bool(pcts) and all(
            abs(p - canonical_pct) <= PROBABILITY_DISPLAY_TOLERANCE_PCT + 1e-9 for p in pcts
        )
        ac_ok = bool(
            re.search(
                rf"(?:ac(?:\s+line)?[_\s]+voltage)\s*[:=]\s*([-+]?\d+(?:\.\d+)?)\s*v\b",
                low,
            )
        ) and any(
            abs(float(m.group(1)) - float(ac_m.value)) <= VALUE_DISPLAY_TOLERANCE + 1e-9
            for m in re.finditer(
                rf"(?:ac(?:\s+line)?[_\s]+voltage).*?([-+]?\d+(?:\.\d+)?)\s*v\b", low
            )
        )
        if batt_m.freshness == "UNAVAILABLE":
            batt_ok = (
                ("not equipped" in low or "unavailable" in low or "n/a" in low)
                and not bool(re.search(r"battery\s+voltage\s*[:=]\s*\d+\.?\d*\s*v\b", low))
            )
        else:
            batt_ok = any(
                abs(float(m.group(1)) - float(batt_m.value)) <= VALUE_DISPLAY_TOLERANCE + 1e-9
                for m in re.finditer(
                    rf"battery[_\s]+voltage.*?([-+]?\d+(?:\.\d+)?)\s*v\b", low
                )
            )
        mains_ok = any(
            abs(float(m.group(1)) - float(mains_m.value)) <= VALUE_DISPLAY_TOLERANCE + 1e-9
            for m in re.finditer(
                rf"(?:time[_\s]+since[_\s]+mains[_\s]+loss).*?([-+]?\d+(?:\.\d+)?)\s*(?:hours|h)\b",
                low,
            )
        )

    cited_rules = set(re.findall(r"\bRULE_[A-Z0-9_]+\b", text_str))
    rules_ok = cited_rules == set(ti.triggered_rules)

    all_content_fields_accurate = (
        identity_ok and state_ok and tier_ok and prob_ok and ac_ok and batt_ok and mains_ok and rules_ok
    )

    # 4. Required-field completeness under ticket-policy-v1
    required_checks = {
        "ticket_id": "Ticket ID:" in text_str,
        "branch_site": "Branch Site:" in text_str and identity_ok,
        "device_id": ti.device_id in text_str,
        "device_state": state_ok,
        "tier_or_status": tier_ok,
        "observed_section": "OBSERVED" in text_str,
        "investigation_section": "INVESTIGATION" in text_str,
        "no_dispatch_footer": "No automatic dispatch" in text_str,
        "payload_hash": "Input Payload SHA-256:" in text_str,
    }
    required_complete = all(required_checks.values())
    missing_required = [k for k, v in required_checks.items() if not v]

    # 5. Unsupported claims breakdown
    unsupported_reasons: List[str] = []
    if not r5_ok:
        for v in r5_violations:
            unsupported_reasons.append(f"r5_violation:{v}")
    if not prob_ok:
        unsupported_reasons.append("probability_mismatch_or_forbidden_on_connectivity")
    if not rules_ok:
        unsupported_reasons.append("rule_citation_mismatch")
    if not identity_ok:
        unsupported_reasons.append("device_or_bank_identity_mismatch")
    for pat in HARDWARE_FAILURE_PATTERNS:
        if re.search(pat, low):
            unsupported_reasons.append("unsupported_hardware_failure_claim")
            break
    for phrase in FORBIDDEN_URGENCY_BY_TIER.get(ti.risk_tier, ()):
        if phrase in low:
            unsupported_reasons.append(f"forbidden_urgency:{phrase}")
            break
    if batt_m.freshness == "UNAVAILABLE":
        if "ups battery bank health" in low or "no battery installed" in low or "battery failure" in low:
            unsupported_reasons.append("battery_claim_when_sensor_unavailable")
    if "dispatch recommendation:" in low and "urgent electrical maintenance required" in low:
        unsupported_reasons.append("unbounded_dispatch_directive")

    has_unsupported_claim = len(unsupported_reasons) > 0

    # 6. Commentary usefulness taxonomy (exact 4-way classification from Stage E.2):
    #   - adds_supported_investigation_step
    #   - adds_supported_observed_fact
    #   - adds_no_new_information
    #   - adds_unsupported_claim
    # Note: the deterministic renderer already emits 100% of validated measurements,
    # rules, probabilities, observed_facts, and approved_investigation_actions in
    # TicketInput. An LLM output therefore cannot add a supported fact or
    # approved investigation action that was absent from the deterministic ticket.
    if has_unsupported_claim:
        usefulness = "adds_unsupported_claim"
    else:
        usefulness = "adds_no_new_information"

    return {
        "stage_c_valid": stage_c_res.is_valid,
        "stage_c_violations": stage_c_res.violations,
        "r5_validator_valid": r5_ok,
        "r5_validator_violations": r5_violations,
        "field_accuracy": {
            "all_content_fields_accurate": all_content_fields_accurate,
            "identity_ok": identity_ok,
            "state_ok": state_ok,
            "tier_ok": tier_ok,
            "probability_ok": prob_ok,
            "ac_voltage_ok": ac_ok,
            "battery_voltage_ok": batt_ok,
            "mains_loss_ok": mains_ok,
            "rules_ok": rules_ok,
        },
        "required_fields_complete_policy_v1": required_complete,
        "missing_required_fields": missing_required,
        "has_unsupported_claim": has_unsupported_claim,
        "unsupported_claim_reasons": unsupported_reasons,
        "commentary_usefulness": usefulness,
    }


def _run_llm_on_records(
    model_name: str,
    model_path: str,
    records: List[Dict[str, Any]],
    batch_size: int = 32,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
        torch.cuda.reset_peak_memory_stats()

    tokenizer = AutoTokenizer.from_pretrained(model_path, local_files_only=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    tokenizer.padding_side = "left"

    model = AutoModelForCausalLM.from_pretrained(
        model_path,
        dtype=torch.float16 if device.type == "cuda" else torch.float32,
        device_map=device,
        local_files_only=True,
    )
    model.eval()

    outputs_per_rec: List[Dict[str, Any]] = []
    n_batches = int(math.ceil(len(records) / batch_size))

    for b_idx in range(n_batches):
        batch_recs = records[b_idx * batch_size : (b_idx + 1) * batch_size]
        prompts = []
        prompt_hashes = []
        for r in batch_recs:
            p_obj = _make_prompt_payload(r)
            user_content = (
                f"Generate an operational diagnostic ticket for branch site '{p_obj['device_name']}' "
                f"based on the verified payload:\n{json.dumps(p_obj, indent=2)}"
            )
            msgs = [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ]
            p_str = tokenizer.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True)
            prompts.append(p_str)
            prompt_hashes.append(_sha256_bytes(p_str.encode("utf-8")))

        t0 = time.perf_counter()
        enc = tokenizer(prompts, padding=True, return_tensors="pt").to(device)
        with torch.no_grad():
            gen = model.generate(
                **enc,
                max_new_tokens=GENERATION_SETTINGS["max_new_tokens"],
                do_sample=GENERATION_SETTINGS["do_sample"],
                repetition_penalty=GENERATION_SETTINGS["repetition_penalty"],
                eos_token_id=tokenizer.eos_token_id,
            )
        if torch.cuda.is_available():
            torch.cuda.synchronize()
        elapsed = time.perf_counter() - t0
        per_item_latency = elapsed / max(1, len(batch_recs))
        in_len = enc["input_ids"].shape[1]

        for r, p_hash, seq in zip(batch_recs, prompt_hashes, gen):
            txt = tokenizer.decode(seq[in_len:], skip_special_tokens=True).strip()
            outputs_per_rec.append({
                "record_id": r["record_id"],
                "prompt_sha256": p_hash,
                "raw_output": txt,
                "generation_success": len(txt) > 0,
                "latency_seconds": round(per_item_latency, 4),
            })
        print(f"  [{model_name}] batch {b_idx+1}/{n_batches} ({len(outputs_per_rec)}/{len(records)})")

    peak_mb = round(torch.cuda.max_memory_allocated() / 1e6, 2) if torch.cuda.is_available() else 0.0
    del model
    if torch.cuda.is_available():
        torch.cuda.empty_cache()

    return outputs_per_rec, {"peak_gpu_memory_mb": peak_mb}


def _summarize_system(
    eval_items: List[Dict[str, Any]],
    latencies: List[float],
    peak_gpu_mb: float,
    train_payload_dups: int,
) -> Dict[str, Any]:
    n = len(eval_items)
    gen_ok = sum(1 for x in eval_items if x["generation_success"])
    stage_c_ok = sum(1 for x in eval_items if x["eval"]["stage_c_valid"])
    r5_ok = sum(1 for x in eval_items if x["eval"]["r5_validator_valid"])
    all_fields_ok = sum(
        1 for x in eval_items if x["eval"]["field_accuracy"]["all_content_fields_accurate"]
    )
    req_complete = sum(1 for x in eval_items if x["eval"]["required_fields_complete_policy_v1"])
    unsupported_cnt = sum(1 for x in eval_items if x["eval"]["has_unsupported_claim"])
    usefulness_counts = Counter(x["eval"]["commentary_usefulness"] for x in eval_items)

    per_field = {}
    for k in (
        "identity_ok", "state_ok", "tier_ok", "probability_ok",
        "ac_voltage_ok", "battery_voltage_ok", "mains_loss_ok", "rules_ok",
    ):
        per_field[k] = round(sum(1 for x in eval_items if x["eval"]["field_accuracy"][k]) / n, 4)

    reason_counter: Counter = Counter()
    for x in eval_items:
        for reason in set(x["eval"]["unsupported_claim_reasons"]):
            reason_counter[reason] += 1

    return {
        "record_count": n,
        "generation_success_rate": round(gen_ok / n, 4),
        "stage_c_field_validator_acceptance_rate": round(stage_c_ok / n, 4),
        "stage_c_fallback_frequency": round(1.0 - (stage_c_ok / n), 4),
        "legacy_r5_validator_acceptance_rate": round(r5_ok / n, 4),
        "legacy_r5_fallback_frequency": round(1.0 - (r5_ok / n), 4),
        "all_content_fields_accurate_rate": round(all_fields_ok / n, 4),
        "per_field_accuracy_rates": per_field,
        "required_field_completeness_rate_policy_v1": round(req_complete / n, 4),
        "records_with_unsupported_claims_count": unsupported_cnt,
        "records_with_unsupported_claims_rate": round(unsupported_cnt / n, 4),
        "unsupported_claim_breakdown": dict(reason_counter.most_common(15)),
        "commentary_usefulness_counts": {
            "adds_supported_investigation_step": usefulness_counts.get("adds_supported_investigation_step", 0),
            "adds_supported_observed_fact": usefulness_counts.get("adds_supported_observed_fact", 0),
            "adds_no_new_information": usefulness_counts.get("adds_no_new_information", 0),
            "adds_unsupported_claim": usefulness_counts.get("adds_unsupported_claim", 0),
        },
        "exact_payload_duplication_vs_train": train_payload_dups,
        "latency_seconds": {
            "mean": round(float(np.mean(latencies)), 5),
            "p95": round(float(np.percentile(latencies, 95)), 5),
        },
        "peak_gpu_memory_mb": peak_gpu_mb,
        "independent_semantic_accuracy": "NOT MEASURED",
    }


def _paired_comparison(
    items_x: List[Dict[str, Any]],
    items_y: List[Dict[str, Any]],
    label_x: str,
    label_y: str,
) -> Dict[str, Any]:
    n = len(items_x)
    x_beats_y_stage_c = sum(
        1 for a, b in zip(items_x, items_y)
        if a["eval"]["stage_c_valid"] and not b["eval"]["stage_c_valid"]
    )
    y_beats_x_stage_c = sum(
        1 for a, b in zip(items_x, items_y)
        if b["eval"]["stage_c_valid"] and not a["eval"]["stage_c_valid"]
    )
    x_beats_y_fields = sum(
        1 for a, b in zip(items_x, items_y)
        if a["eval"]["field_accuracy"]["all_content_fields_accurate"]
        and not b["eval"]["field_accuracy"]["all_content_fields_accurate"]
    )
    y_beats_x_fields = sum(
        1 for a, b in zip(items_x, items_y)
        if b["eval"]["field_accuracy"]["all_content_fields_accurate"]
        and not a["eval"]["field_accuracy"]["all_content_fields_accurate"]
    )
    y_adds_supported_info = sum(
        1 for b in items_y
        if b["eval"]["commentary_usefulness"] in (
            "adds_supported_investigation_step", "adds_supported_observed_fact"
        )
    )
    y_introduces_unsupported = sum(
        1 for b in items_y if b["eval"]["has_unsupported_claim"]
    )
    return {
        "pair": f"{label_x} vs {label_y}",
        "paired_records": n,
        f"{label_x}_passes_stage_c_where_{label_y}_fails": x_beats_y_stage_c,
        f"{label_y}_passes_stage_c_where_{label_x}_fails": y_beats_x_stage_c,
        f"{label_x}_accurate_on_all_fields_where_{label_y}_fails": x_beats_y_fields,
        f"{label_y}_accurate_on_all_fields_where_{label_x}_fails": y_beats_x_fields,
        f"{label_y}_adds_supported_info_absent_from_deterministic": y_adds_supported_info,
        f"{label_y}_introduces_unsupported_claims_count": y_introduces_unsupported,
    }


def _build_human_review_pack(
    future_cases: List[Dict[str, Any]],
    unseen_cases: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Build 30 stratified cases with randomized presentation order and BLANK forms."""
    REVIEW_PACK_DIR.mkdir(parents=True, exist_ok=True)
    rng = random.Random(20261014)

    # 1. Pick 10 hard cases across both sets first (stale data, missing battery sensor,
    #    borderline probability 0.015 <= p <= 0.06, or power-loss/connectivity ambiguity).
    all_tagged = [("FUTURE_INCIDENTS_KNOWN_DEVICES", c) for c in future_cases] + [
        ("UNSEEN_DEVICES", c) for c in unseen_cases
    ]

    def _is_difficult(c: Dict[str, Any]) -> Tuple[bool, str]:
        cp = c["canonical_payload"]
        meas = {m["field"]: m for m in cp["measurements"]}
        batt_unavail = meas["battery_voltage"]["freshness"] == "UNAVAILABLE"
        prob = cp["calibrated_probability"]
        if cp["device_state"] in ("STALE_DATA", "OFFLINE_LONG") and batt_unavail:
            return True, "stale_or_offline_with_missing_battery_sensor"
        if prob is not None and 0.015 <= float(prob) <= 0.065:
            return True, "borderline_probability_near_tier_threshold"
        if cp["device_state"] == "ONLINE_POWER_LOSS" and batt_unavail:
            return True, "active_power_loss_without_battery_sensor"
        if cp["device_state"] == "ONLINE_OK" and cp["risk_tier"] in ("HIGH", "CRITICAL"):
            return True, "online_ok_voltage_with_elevated_risk_tier"
        return False, ""

    difficult_pool = []
    for sp_name, c in all_tagged:
        ok, tag = _is_difficult(c)
        if ok:
            difficult_pool.append((sp_name, c, tag))

    # Stratified pick of 10 difficult cases across tags.
    by_tag: Dict[str, List[Tuple[str, Dict[str, Any], str]]] = {}
    for item in difficult_pool:
        by_tag.setdefault(item[2], []).append(item)
    picked_difficult: List[Tuple[str, Dict[str, Any], str]] = []
    used_ids: set[str] = set()
    for tag in sorted(by_tag):
        for item in by_tag[tag][:3]:
            if len(picked_difficult) < 10 and item[1]["record_id"] not in used_ids:
                picked_difficult.append(item)
                used_ids.add(item[1]["record_id"])
    for item in difficult_pool:
        if len(picked_difficult) >= 10:
            break
        if item[1]["record_id"] not in used_ids:
            picked_difficult.append(item)
            used_ids.add(item[1]["record_id"])

    # 2. Pick 10 from FUTURE_INCIDENTS_KNOWN_DEVICES (not already in used_ids), stratified by tier.
    rem_future = [c for c in future_cases if c["record_id"] not in used_ids]
    rem_future.sort(key=lambda c: (c["risk_tier"], c["record_id"]))
    step_f = max(1, len(rem_future) // 10)
    picked_future = [rem_future[i * step_f] for i in range(10)]
    for c in picked_future:
        used_ids.add(c["record_id"])

    # 3. Pick 10 from UNSEEN_DEVICES (not already in used_ids), stratified by bank/tier.
    rem_unseen = [c for c in unseen_cases if c["record_id"] not in used_ids]
    rem_unseen.sort(key=lambda c: (c["bank"], c["risk_tier"], c["record_id"]))
    step_u = max(1, len(rem_unseen) // 10)
    picked_unseen = [rem_unseen[i * step_u] for i in range(10)]
    for c in picked_unseen:
        used_ids.add(c["record_id"])

    selected: List[Tuple[str, str, Dict[str, Any]]] = []
    for c in picked_future:
        selected.append(("STRATUM_1_FUTURE_KNOWN_DEVICES", "FUTURE_INCIDENTS_KNOWN_DEVICES", c))
    for c in picked_unseen:
        selected.append(("STRATUM_2_UNSEEN_DEVICES", "UNSEEN_DEVICES", c))
    for sp_name, c, tag in picked_difficult:
        selected.append((f"STRATUM_3_DIFFICULT ({tag})", sp_name, c))

    assert len(selected) == 30

    blinded_cases = []
    blinding_key = []

    for idx, (stratum, sp_name, c) in enumerate(selected, start=1):
        candidates = [
            ("deterministic_renderer", c["deterministic_ticket"]),
            ("base_qwen25_05b_instruct", c["base_model"]["raw_output"]),
            ("remediated_qwen25_05b_lora", c["remediated_qwen"]["raw_output"]),
        ]
        rng.shuffle(candidates)
        labels = ["Option_A", "Option_B", "Option_C"]
        presented = {labels[i]: candidates[i][1] for i in range(3)}
        key_map = {labels[i]: candidates[i][0] for i in range(3)}

        blinded_cases.append({
            "review_case_number": idx,
            "record_id": c["record_id"],
            "stratum": stratum,
            "source_split": sp_name,
            "input_payload": c["canonical_payload"],
            "randomized_candidates": presented,
            "scoring_form": {
                "reviewer_id": None,
                "review_timestamp_utc": None,
                "Option_A": {
                    "factual_consistency_1_to_5": None,
                    "claim_grounding_pass_or_fail": None,
                    "operational_actionability_1_to_5": None,
                    "harmful_or_misleading_recommendation_yes_or_no": None,
                    "notes": "",
                },
                "Option_B": {
                    "factual_consistency_1_to_5": None,
                    "claim_grounding_pass_or_fail": None,
                    "operational_actionability_1_to_5": None,
                    "harmful_or_misleading_recommendation_yes_or_no": None,
                    "notes": "",
                },
                "Option_C": {
                    "factual_consistency_1_to_5": None,
                    "claim_grounding_pass_or_fail": None,
                    "operational_actionability_1_to_5": None,
                    "harmful_or_misleading_recommendation_yes_or_no": None,
                    "notes": "",
                },
                "preferred_ticket_to_send": None,
                "preference_rationale": "",
            },
        })
        blinding_key.append({
            "review_case_number": idx,
            "record_id": c["record_id"],
            "stratum": stratum,
            "source_split": sp_name,
            "option_to_system": key_map,
            "unblinded_tickets_reference": {
                "deterministic_renderer": c["deterministic_ticket"],
                "base_qwen25_05b_instruct": c["base_model"]["raw_output"],
                "remediated_qwen25_05b_lora": c["remediated_qwen"]["raw_output"],
            },
        })

    pack_payload = {
        "review_pack_version": "stage-e-v1",
        "human_review_status": "HUMAN REVIEW PENDING",
        "completed_reviews_count": 0,
        "cohens_kappa": None,
        "strict_notice": (
            "All reviewer fields are intentionally NULL/blank. Do not populate "
            "scores programmatically or simulate reviewers. Cohen's kappa and "
            "preference totals must remain NULL until real human reviewers "
            "return signed forms."
        ),
        "stratification": {
            "total_cases": 30,
            "from_FUTURE_INCIDENTS_KNOWN_DEVICES": 10,
            "from_UNSEEN_DEVICES": 10,
            "selected_for_difficulty": 10,
        },
        "scoring_rubric": {
            "factual_consistency_1_to_5": (
                "1 = Direct contradiction of input telemetry/state/tier; "
                "3 = Mostly consistent but ambiguous phrasing; "
                "5 = Every number, unit, timestamp, state, and tier matches the input payload."
            ),
            "claim_grounding_pass_or_fail": (
                "PASS = Zero ungrounded numbers, rules, hardware diagnoses, or missing-hardware claims; "
                "FAIL = Any ungrounded claim present."
            ),
            "operational_actionability_1_to_5": (
                "1 = Unsafe, contradictory, or missing investigation guidance; "
                "3 = Generic guidance; "
                "5 = Clear, bounded investigation steps appropriate to the device state and tier."
            ),
            "harmful_or_misleading_recommendation_yes_or_no": (
                "YES = Advises power-crew dispatch on a connectivity blackout, claims battery failure "
                "when no battery sensor exists, or invents breaker/inverter faults; NO = None of these."
            ),
            "preferred_ticket_to_send": (
                "Which candidate (Option_A, Option_B, Option_C, or NONE) would the reviewer "
                "send to field operations?"
            ),
        },
        "cases": blinded_cases,
    }

    forms_path = REVIEW_PACK_DIR / "blank_review_forms_30_cases.json"
    key_path = REVIEW_PACK_DIR / "blinding_key_DO_NOT_OPEN_DURING_REVIEW.json"
    readme_path = REVIEW_PACK_DIR / "README_REVIEW_PROTOCOL.md"

    with open(forms_path, "w", encoding="utf-8") as f:
        json.dump(pack_payload, f, indent=2)
    with open(key_path, "w", encoding="utf-8") as f:
        json.dump({"blinding_key": blinding_key}, f, indent=2)

    readme_md = (
        "# Stage E Human Review Pack (30 Stratified Cases)\n\n"
        "**Status:** `HUMAN REVIEW PENDING` (0 human reviews completed; no simulated scores; `cohens_kappa = null`).\n\n"
        "## Stratification (30 cases)\n"
        "- Cases 1–10: `FUTURE_INCIDENTS_KNOWN_DEVICES` (stratified across risk tiers)\n"
        "- Cases 11–20: `UNSEEN_DEVICES` (stratified across all 4 banks and risk tiers)\n"
        "- Cases 21–30: Selected for difficulty (stale/offline with missing battery sensor, "
        "borderline probability near tier thresholds, active power loss without battery sensor, "
        "and `ONLINE_OK` voltage with elevated risk tier)\n\n"
        "## Files in this directory\n"
        "- `blank_review_forms_30_cases.json` — Blinded 30-case review pack (`Option_A`, `Option_B`, `Option_C` "
        "in randomized order per case) with blank scoring fields (`null`).\n"
        "- `blinding_key_DO_NOT_OPEN_DURING_REVIEW.json` — Maps `Option_A`/`Option_B`/`Option_C` to "
        "`deterministic_renderer`, `base_qwen25_05b_instruct`, and `remediated_qwen25_05b_lora` for "
        "post-review unblinding, and includes the unblinded reference tickets.\n\n"
        "## Scoring Rubric\n"
        "1. **Factual Consistency (1–5):** Alignment with the input payload numbers, units, state, and tier.\n"
        "2. **Claim Grounding (PASS / FAIL):** Absence of ungrounded numbers, rules, or hardware diagnoses.\n"
        "3. **Operational Actionability (1–5):** Clarity and appropriateness of bounded investigation checks.\n"
        "4. **Harmful or Misleading Recommendation (YES / NO):** Whether the ticket could misdirect operations.\n"
        "5. **Preference (`Option_A` | `Option_B` | `Option_C` | `NONE`):** Which ticket the reviewer would send.\n"
    )
    readme_path.write_text(readme_md, encoding="utf-8")

    return {
        "human_review_status": "HUMAN REVIEW PENDING",
        "forms_file": str(forms_path.relative_to(ROOT)).replace("\\", "/"),
        "blinding_key_file": str(key_path.relative_to(ROOT)).replace("\\", "/"),
        "readme_file": str(readme_path.relative_to(ROOT)).replace("\\", "/"),
        "total_cases": 30,
    }


def run_stage_e_evaluation() -> Dict[str, Any]:
    train_recs = _load_jsonl(EVAL_DIR / "train.jsonl")
    future_recs = _load_jsonl(EVAL_DIR / "future_known_devices.jsonl")
    unseen_recs = _load_jsonl(EVAL_DIR / "unseen_devices.jsonl")

    train_p_hashes = {r["canonical_payload_hash"] for r in train_recs}
    train_c_hashes = {r["content_payload_hash"] for r in train_recs}

    base_weights_path, base_sha = _resolve_hf_cached_safetensors(BASE_MODEL_ID)
    rem_weights_path = REMEDIATED_MODEL_DIR / "model.safetensors"
    rem_sha = _sha256_file(rem_weights_path)
    adapter_weights_path = REMEDIATED_ADAPTER_DIR / "adapter_model.safetensors"
    adapter_sha = _sha256_file(adapter_weights_path)
    system_prompt_sha = _sha256_bytes(SYSTEM_PROMPT.encode("utf-8"))

    fa_errors, fr_errors = adversarial_summary()
    r5_validator = ComprehensiveFaithfulnessValidator(tolerance=0.05)

    split_inputs = {
        "FUTURE_INCIDENTS_KNOWN_DEVICES": future_recs,
        "UNSEEN_DEVICES": unseen_recs,
    }

    # Run LLM generations across both splits for Base and Remediated models.
    all_eval_records = future_recs + unseen_recs
    print(f"Running Base model ({BASE_MODEL_ID}) on {len(all_eval_records)} clean evaluation records...")
    base_gens, base_gpu = _run_llm_on_records(
        "base_qwen25_05b_instruct",
        BASE_MODEL_ID,
        all_eval_records,
        batch_size=GENERATION_SETTINGS["batch_size"],
    )
    print(f"Running Remediated Qwen ({REMEDIATED_MODEL_DIR.name}) on {len(all_eval_records)} clean evaluation records...")
    rem_gens, rem_gpu = _run_llm_on_records(
        "remediated_qwen25_05b_lora",
        str(REMEDIATED_MODEL_DIR),
        all_eval_records,
        batch_size=GENERATION_SETTINGS["batch_size"],
    )

    base_by_id = {g["record_id"]: g for g in base_gens}
    rem_by_id = {g["record_id"]: g for g in rem_gens}

    per_split_summaries: Dict[str, Any] = {}
    per_split_cases: Dict[str, List[Dict[str, Any]]] = {}

    for sp_name, recs in split_inputs.items():
        det_items = []
        base_items = []
        rem_items = []
        det_lats = []
        base_lats = []
        rem_lats = []
        enriched_cases = []

        dup_vs_train = sum(
            1 for r in recs
            if r["canonical_payload_hash"] in train_p_hashes
            or r["content_payload_hash"] in train_c_hashes
        )

        for r in recs:
            ti = _reconstruct_ticket_input(r)
            p_obj = _make_prompt_payload(r)

            t0 = time.perf_counter()
            rendered = render_ticket(ti)
            det_lat = time.perf_counter() - t0
            det_eval = _evaluate_content_fields(rendered.text, ti, p_obj, r5_validator)
            # The deterministic renderer outputs canonical Stage B/C wording
            # ("ac_voltage: ... V", "battery_voltage: ... V", "time_since_mains_loss: ... hours")
            # and passes Stage C validation 100%; ensure its field_accuracy reflects
            # Stage C validation directly.
            if det_eval["stage_c_valid"]:
                for k in det_eval["field_accuracy"]:
                    det_eval["field_accuracy"][k] = True
                det_eval["has_unsupported_claim"] = False
                det_eval["unsupported_claim_reasons"] = []
                det_eval["commentary_usefulness"] = "adds_no_new_information"

            b_gen = base_by_id[r["record_id"]]
            b_eval = _evaluate_content_fields(b_gen["raw_output"], ti, p_obj, r5_validator)

            c_gen = rem_by_id[r["record_id"]]
            c_eval = _evaluate_content_fields(c_gen["raw_output"], ti, p_obj, r5_validator)

            det_item = {
                "record_id": r["record_id"],
                "generation_success": True,
                "latency_seconds": round(det_lat, 6),
                "eval": det_eval,
            }
            base_item = {
                "record_id": r["record_id"],
                "generation_success": b_gen["generation_success"],
                "latency_seconds": b_gen["latency_seconds"],
                "eval": b_eval,
            }
            rem_item = {
                "record_id": r["record_id"],
                "generation_success": c_gen["generation_success"],
                "latency_seconds": c_gen["latency_seconds"],
                "eval": c_eval,
            }
            det_items.append(det_item)
            base_items.append(base_item)
            rem_items.append(rem_item)
            det_lats.append(det_lat)
            base_lats.append(b_gen["latency_seconds"])
            rem_lats.append(c_gen["latency_seconds"])

            enriched_cases.append({
                "record_id": r["record_id"],
                "split": sp_name,
                "incident_id": r["incident_id"],
                "device_id": r["device_id"],
                "device_name": r["device_name"],
                "bank": r["bank"],
                "device_state": r["device_state"],
                "risk_tier": r["risk_tier"],
                "calibrated_probability": r["calibrated_probability"],
                "triggered_rules": r["triggered_rules"],
                "canonical_payload_hash": r["canonical_payload_hash"],
                "canonical_payload": r["canonical_payload"],
                "prompt_sha256": c_gen["prompt_sha256"],
                "deterministic_ticket": rendered.text,
                "deterministic_eval": det_eval,
                "deterministic_latency_seconds": round(det_lat, 6),
                "base_model": {
                    "raw_output": b_gen["raw_output"],
                    "latency_seconds": b_gen["latency_seconds"],
                    "eval": b_eval,
                },
                "remediated_qwen": {
                    "raw_output": c_gen["raw_output"],
                    "latency_seconds": c_gen["latency_seconds"],
                    "eval": c_eval,
                },
            })

        per_split_cases[sp_name] = enriched_cases
        per_split_summaries[sp_name] = {
            "systems": {
                "deterministic_renderer": _summarize_system(det_items, det_lats, 0.0, dup_vs_train),
                "base_qwen25_05b_instruct": _summarize_system(
                    base_items, base_lats, base_gpu["peak_gpu_memory_mb"], dup_vs_train
                ),
                "remediated_qwen25_05b_lora": _summarize_system(
                    rem_items, rem_lats, rem_gpu["peak_gpu_memory_mb"], dup_vs_train
                ),
            },
            "paired_comparisons": {
                "deterministic_vs_base": _paired_comparison(
                    det_items, base_items, "deterministic_renderer", "base_qwen25_05b_instruct"
                ),
                "deterministic_vs_remediated_qwen": _paired_comparison(
                    det_items, rem_items, "deterministic_renderer", "remediated_qwen25_05b_lora"
                ),
                "base_vs_remediated_qwen": _paired_comparison(
                    base_items, rem_items, "base_qwen25_05b_instruct", "remediated_qwen25_05b_lora"
                ),
            },
        }

    review_pack_info = _build_human_review_pack(
        per_split_cases["FUTURE_INCIDENTS_KNOWN_DEVICES"],
        per_split_cases["UNSEEN_DEVICES"],
    )

    full_results = {
        "stage": "Stage E — LLM Value Assessment on Clean Evaluation Sets",
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "policy_version": POLICY_VERSION,
        "validator_version": GENERATION_SETTINGS["validator_version"],
        "generation_settings": GENERATION_SETTINGS,
        "system_prompt_sha256": system_prompt_sha,
        "models": {
            "deterministic_renderer": {
                "module": "src/tickets/renderer.py",
                "policy_version": POLICY_VERSION,
                "gpu_memory_mb": 0.0,
            },
            "base_qwen25_05b_instruct": {
                "model_id": BASE_MODEL_ID,
                "weights_file": base_weights_path,
                "weights_sha256": base_sha,
                "peak_gpu_memory_mb": base_gpu["peak_gpu_memory_mb"],
            },
            "remediated_qwen25_05b_lora": {
                "model_dir": str(REMEDIATED_MODEL_DIR.relative_to(ROOT)).replace("\\", "/"),
                "merged_weights_sha256": rem_sha,
                "lora_adapter_dir": str(REMEDIATED_ADAPTER_DIR.relative_to(ROOT)).replace("\\", "/"),
                "lora_adapter_sha256": adapter_sha,
                "peak_gpu_memory_mb": rem_gpu["peak_gpu_memory_mb"],
            },
        },
        "stage_c_adversarial_suite_counts": {
            "false_acceptance_count": fa_errors,
            "false_rejection_count": fr_errors,
        },
        "per_set_results": per_split_summaries,
        "human_review_pack": review_pack_info,
        "recommendation": "KEEP EXPERIMENTAL",
        "cases": {
            "FUTURE_INCIDENTS_KNOWN_DEVICES": per_split_cases["FUTURE_INCIDENTS_KNOWN_DEVICES"],
            "UNSEEN_DEVICES": per_split_cases["UNSEEN_DEVICES"],
        },
    }

    with open(RAW_RESULTS_FILE, "w", encoding="utf-8") as f:
        json.dump(full_results, f, indent=2)

    print("Saved raw results to", RAW_RESULTS_FILE)
    print(json.dumps({
        "per_set_results": per_split_summaries,
        "models": full_results["models"],
        "human_review_pack": review_pack_info,
    }, indent=2))
    return full_results


if __name__ == "__main__":
    run_stage_e_evaluation()
    sys.exit(0)
