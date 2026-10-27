#!/usr/bin/env python
"""Deep testing and benchmarking suite for the fine-tuned ThingsBoard causal reasoning model.

Evaluates:
  1. Outage prediction accuracy and risk tier alignment on held-out validation devices.
  2. Chain-of-Thought reasoning structure and causal integrity.
  3. Inference latency, token generation throughput, and GPU VRAM footprint.
  4. Stress-testing against edge cases (brownouts, overvoltage surges, sensor dropouts).

Outputs:
  - audit_reports/reasoning_model_deep_test_report.json
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
import re
import sys
import time

# UTF-8 stdout configuration for Windows consoles
if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr.encoding != "utf-8":
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import numpy as np
import torch
from transformers import AutoModelForCausalLM, AutoTokenizer

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent
MODEL_PATH = ROOT / "models/qwen_reasoning_outage_predictor/final_model"
VAL_PATH = ROOT / "training_data/mcp_reasoning_val.jsonl"
OUT_REPORT = ROOT / "audit_reports/reasoning_model_deep_test_report.json"


def extract_prediction_fields(output_text: str) -> dict:
    has_reasoning = "<reasoning>" in output_text and ("</reasoning>" in output_text or "### " in output_text)

    # 1. Extract risk tier (supports structured markdown header or CoT inline ratings like '100.0 (CRITICAL)')
    tier_match = re.search(r"(?:Risk (?:Classification|Tier|Priority|rating evaluates to|score evaluates to)[^\n]*?|\bTier:\s*)(CRITICAL|HIGH|MEDIUM|HEALTHY)", output_text, re.IGNORECASE)
    if not tier_match:
        # Fallback search for any prominent tier keyword in findings/reasoning
        if re.search(r"\bCRITICAL\b", output_text, re.IGNORECASE):
            pred_tier = "CRITICAL"
        elif re.search(r"\bHIGH\b", output_text, re.IGNORECASE):
            pred_tier = "HIGH"
        elif re.search(r"\bMEDIUM\b", output_text, re.IGNORECASE):
            pred_tier = "MEDIUM"
        elif re.search(r"\bHEALTHY\b", output_text, re.IGNORECASE):
            pred_tier = "HEALTHY"
        else:
            pred_tier = "UNKNOWN"
    else:
        pred_tier = tier_match.group(1).upper()

    # 2. Extract probability if stated
    prob_match = re.search(r"(?:Outage Probability|Failure Probability|Likelihood|disruption due to electrical reasons is|service disruption[^\n]*?is)\s*[:=]?\s*\*?\*?(\d{1,2}(?:\.\d+)?)\s*%", output_text, re.IGNORECASE)
    pred_prob = float(prob_match.group(1)) if prob_match else None

    # 3. Extract 24h outage verdict
    outage_match = re.search(r"24-Hour Outage (?:Prediction|Likelihood)\*?\*?:\s*\*?\*?(POSITIVE|NEGATIVE)", output_text, re.IGNORECASE)
    if outage_match:
        pred_outage = outage_match.group(1).upper()
    elif pred_prob is not None:
        pred_outage = "POSITIVE" if pred_prob >= 50.0 else "NEGATIVE"
    elif pred_tier in ("CRITICAL", "HIGH"):
        pred_outage = "POSITIVE"
    elif pred_tier in ("MEDIUM", "HEALTHY"):
        pred_outage = "NEGATIVE"
    else:
        pred_outage = "UNKNOWN"

    # Check causal elements in reasoning
    has_electrical = any(k in output_text.lower() for k in ["ac voltage", "battery", "dc bus", "mains", "power", "electrical", "voltage"])
    has_subsystem = any(k in output_text.lower() for k in ["nvr", "fire alarm", "fas", "storage", "hdd", "camera", "transmission", "sensor"])
    has_dispatch = any(k in output_text.lower() for k in ["dispatch", "mitigation", "technician", "preventative", "ticket", "priority", "surveillance"])

    return {
        "has_reasoning_tags": has_reasoning,
        "predicted_risk_tier": pred_tier,
        "predicted_outage_verdict": pred_outage,
        "predicted_probability": pred_prob,
        "has_electrical_analysis": has_electrical,
        "has_subsystem_analysis": has_subsystem,
        "has_actionable_dispatch": has_dispatch,
    }


def run_benchmark():
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    logger.info(f"Loading model on compute device: {device}...")
    if device.type == "cuda":
        torch.cuda.reset_peak_memory_stats()

    start_load = time.time()
    tokenizer = AutoTokenizer.from_pretrained(str(MODEL_PATH), use_fast=True)
    model = AutoModelForCausalLM.from_pretrained(
        str(MODEL_PATH),
        dtype=torch.float16 if device.type == "cuda" else torch.float32,
        device_map=device,
    )
    load_time = time.time() - start_load
    logger.info(f"Model loaded successfully in {load_time:.2f}s.")

    # 1. Validation Set Evaluation
    logger.info(f"Reading validation set from {VAL_PATH}...")
    val_records = []
    with open(VAL_PATH, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                val_records.append(json.loads(line))

    # Evaluate on a comprehensive sample across variants (30 test items across tiers)
    test_sample = val_records[:30]
    logger.info(f"Benchmarking on {len(test_sample)} validation dialogues...")

    sample_results = []
    latencies = []
    token_throughputs = []
    correct_tiers = 0
    correct_outages = 0
    valid_reasonings = 0

    for i, item in enumerate(test_sample):
        msgs = item["messages"]
        ground_tier = item["risk_tier"]
        ground_outage = "POSITIVE" if "POSITIVE" in item["outage_pred"] else "NEGATIVE"

        prompt_msgs = [m for m in msgs if m["role"] in ("system", "user")]
        chat_prompt = tokenizer.apply_chat_template(prompt_msgs, tokenize=False, add_generation_prompt=True)
        inputs = tokenizer(chat_prompt, return_tensors="pt").to(device)

        t0 = time.perf_counter()
        with torch.no_grad():
            gen_tokens = model.generate(
                **inputs,
                max_new_tokens=400,
                temperature=0.2,
                top_p=0.9,
                repetition_penalty=1.1,
                do_sample=True,
                eos_token_id=tokenizer.eos_token_id,
            )
        elapsed = time.perf_counter() - t0

        new_tokens_count = gen_tokens.shape[1] - inputs["input_ids"].shape[1]
        throughput = new_tokens_count / max(0.001, elapsed)

        latencies.append(elapsed)
        token_throughputs.append(throughput)

        out_text = tokenizer.decode(gen_tokens[0][inputs["input_ids"].shape[1]:], skip_special_tokens=True)
        parsed = extract_prediction_fields(out_text)

        tier_match = parsed["predicted_risk_tier"] == ground_tier
        outage_match = parsed["predicted_outage_verdict"] == ground_outage

        if tier_match:
            correct_tiers += 1
        if outage_match:
            correct_outages += 1
        if parsed["has_reasoning_tags"]:
            valid_reasonings += 1

        sample_results.append({
            "device_id": item["device_id"],
            "device_name": item["device_name"],
            "ground_risk_tier": ground_tier,
            "predicted_risk_tier": parsed["predicted_risk_tier"],
            "tier_match": tier_match,
            "ground_outage": ground_outage,
            "predicted_outage": parsed["predicted_outage_verdict"],
            "outage_match": outage_match,
            "latency_sec": round(elapsed, 3),
            "tokens_generated": new_tokens_count,
            "tokens_per_sec": round(throughput, 1),
            "has_reasoning": parsed["has_reasoning_tags"],
            "causal_coverage": {
                "electrical": parsed["has_electrical_analysis"],
                "subsystem": parsed["has_subsystem_analysis"],
                "dispatch": parsed["has_actionable_dispatch"],
            },
        })

        if (i + 1) % 10 == 0:
            logger.info(f"Progress: {i + 1}/{len(test_sample)} tested. Mean Latency: {np.mean(latencies):.2f}s | Throughput: {np.mean(token_throughputs):.1f} tok/s")

    # 2. Extreme Edge-Case Stress Testing
    logger.info("Executing edge-case operational stress tests...")
    stress_cases = [
        {
            "name": "Case 1: Severe Utility Blackout & Depleted Battery",
            "prompt": (
                "Perform a 24-hour predictive outage and risk assessment for branch device 'STRESS-BLACKOUT-01'.\n"
                "Observed Telemetry Snapshot:\n"
                "- AC Line Voltage: 0.0 V\n"
                "- Battery Bus Voltage: 9.8 V (State of Charge: 5%)\n"
                "- System Draw: 0.10 A\n"
                "- Primary Power State: MAINS DISCONNECTED / POWER LOSS\n"
                "- Surveillance NVR Status: OFFLINE (CCTV Stream Interrupted)\n"
                "- Fire Alarm System (FAS): NORMAL (Supervisory Clear)\n"
                "- Storage Health: HEALTHY (SMART Verified)\n"
                "- Alarm Profile: 40 Critical, 15 Major, 0 Warning, 5 Active Unacknowledged.\n\n"
                "Provide your step-by-step causal reasoning, root cause analysis, 24-hour outage prediction, and recommended dispatch actions."
            ),
            "expected_tier": "CRITICAL",
            "expected_outage": "POSITIVE",
        },
        {
            "name": "Case 2: Pure Nominal Operation (Healthy Control)",
            "prompt": (
                "Perform a 24-hour predictive outage and risk assessment for branch device 'STRESS-HEALTHY-01'.\n"
                "Observed Telemetry Snapshot:\n"
                "- AC Line Voltage: 231.2 V\n"
                "- Battery Bus Voltage: 13.6 V (State of Charge: 98%)\n"
                "- System Draw: 1.45 A\n"
                "- Primary Power State: MAINS ACTIVE (Healthy AC)\n"
                "- Surveillance NVR Status: ONLINE (Normal Recording)\n"
                "- Fire Alarm System (FAS): NORMAL (Supervisory Clear)\n"
                "- Storage Health: HEALTHY (SMART Verified)\n"
                "- Alarm Profile: 0 Critical, 0 Major, 0 Warning, 0 Active Unacknowledged.\n\n"
                "Provide your step-by-step causal reasoning, root cause analysis, 24-hour outage prediction, and recommended dispatch actions."
            ),
            "expected_tier": "HEALTHY",
            "expected_outage": "NEGATIVE",
        },
        {
            "name": "Case 3: Severe Overvoltage Grid Surge (315V AC)",
            "prompt": (
                "Perform a 24-hour predictive outage and risk assessment for branch device 'STRESS-SURGE-01'.\n"
                "Observed Telemetry Snapshot:\n"
                "- AC Line Voltage: 315.0 V\n"
                "- Battery Bus Voltage: 14.8 V (Overvoltage Warning)\n"
                "- System Draw: 4.80 A\n"
                "- Primary Power State: Inverter / Standby Mode\n"
                "- Surveillance NVR Status: ONLINE (Normal Recording)\n"
                "- Fire Alarm System (FAS): NORMAL (Supervisory Clear)\n"
                "- Storage Health: STORAGE HARDWARE FAULT\n"
                "- Alarm Profile: 18 Critical, 8 Major, 0 Warning, 2 Active Unacknowledged.\n\n"
                "Provide your step-by-step causal reasoning, root cause analysis, 24-hour outage prediction, and recommended dispatch actions."
            ),
            "expected_tier": "CRITICAL",
            "expected_outage": "POSITIVE",
        },
        {
            "name": "Case 4: Mild Elevated Telemetry Jitter",
            "prompt": (
                "Perform a 24-hour predictive outage and risk assessment for branch device 'STRESS-JITTER-01'.\n"
                "Observed Telemetry Snapshot:\n"
                "- AC Line Voltage: 219.0 V\n"
                "- Battery Bus Voltage: 12.8 V (State of Charge: 82%)\n"
                "- System Draw: 1.60 A\n"
                "- Primary Power State: MAINS ACTIVE (Healthy AC)\n"
                "- Surveillance NVR Status: ONLINE (Normal Recording)\n"
                "- Fire Alarm System (FAS): NORMAL (Supervisory Clear)\n"
                "- Storage Health: HEALTHY (SMART Verified)\n"
                "- Alarm Profile: 0 Critical, 1 Major, 4 Warning, 0 Active Unacknowledged.\n\n"
                "Provide your step-by-step causal reasoning, root cause analysis, 24-hour outage prediction, and recommended dispatch actions."
            ),
            "expected_tier": "MEDIUM",
            "expected_outage": "NEGATIVE",
        },
    ]

    stress_results = []
    for sc in stress_cases:
        msgs = [
            {"role": "system", "content": "You are the Antigravity ThingsBoard Fleet Intelligence AI."},
            {"role": "user", "content": sc["prompt"]},
        ]
        chat_prompt = tokenizer.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True)
        inputs = tokenizer(chat_prompt, return_tensors="pt").to(device)

        t0 = time.perf_counter()
        with torch.no_grad():
            gen_tokens = model.generate(
                **inputs,
                max_new_tokens=420,
                temperature=0.2,
                top_p=0.9,
                repetition_penalty=1.1,
                do_sample=True,
                eos_token_id=tokenizer.eos_token_id,
            )
        elapsed = time.perf_counter() - t0
        out_text = tokenizer.decode(gen_tokens[0][inputs["input_ids"].shape[1]:], skip_special_tokens=True)
        parsed = extract_prediction_fields(out_text)

        tier_pass = parsed["predicted_risk_tier"] == sc["expected_tier"]
        outage_pass = parsed["predicted_outage_verdict"] == sc["expected_outage"]

        stress_results.append({
            "case_name": sc["name"],
            "expected_tier": sc["expected_tier"],
            "predicted_tier": parsed["predicted_risk_tier"],
            "tier_passed": tier_pass,
            "expected_outage": sc["expected_outage"],
            "predicted_outage": parsed["predicted_outage_verdict"],
            "outage_passed": outage_pass,
            "has_reasoning": parsed["has_reasoning_tags"],
            "latency_sec": round(elapsed, 3),
            "generated_output_snippet": out_text[:350] + "...",
        })
        logger.info(f"Stress test '{sc['name']}': Tier Pass={tier_pass} ({parsed['predicted_risk_tier']}), Outage Pass={outage_pass} ({parsed['predicted_outage_verdict']})")

    peak_vram_gb = torch.cuda.max_memory_allocated() / (1024**3) if device.type == "cuda" else 0.0

    summary_metrics = {
        "model_path": str(MODEL_PATH),
        "total_test_samples": len(test_sample),
        "outage_prediction_accuracy_pct": round(correct_outages / len(test_sample) * 100, 2),
        "risk_tier_exact_match_pct": round(correct_tiers / len(test_sample) * 100, 2),
        "reasoning_tag_compliance_pct": round(valid_reasonings / len(test_sample) * 100, 2),
        "mean_latency_sec": round(float(np.mean(latencies)), 3),
        "p95_latency_sec": round(float(np.percentile(latencies, 95)), 3),
        "mean_throughput_tokens_per_sec": round(float(np.mean(token_throughputs)), 1),
        "peak_gpu_vram_gb": round(peak_vram_gb, 2),
        "edge_cases_total": len(stress_cases),
        "edge_cases_passed": sum(1 for s in stress_results if s["outage_passed"] and s["tier_passed"]),
    }

    full_report = {
        "summary_metrics": summary_metrics,
        "sample_evaluations": sample_results,
        "stress_case_results": stress_results,
    }

    OUT_REPORT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_REPORT, "w", encoding="utf-8") as f:
        json.dump(full_report, f, indent=2)

    logger.info(f"Deep testing report successfully banked at {OUT_REPORT}")
    return summary_metrics, stress_results


if __name__ == "__main__":
    run_benchmark()
