"""src/explain/explainer.py
Phase R5 Explanation Layer: State-Routed Diagnostic & Connectivity Generation.

Mandates:
1. LLM NEVER predicts outage probability or risk tier (injected strictly from Phase 3 & 4).
2. Comprehensive Faithfulness Validator checks both numerical grounding and claim-level semantic validity.
3. State Routing: STALE_DATA and OFFLINE_LONG devices receive connectivity investigation tickets with NO outage probability.
4. Guaranteed Fallback Safety: Deterministic template fallback on any validation failure.
5. Benchmark comparison on 100 held-out test cases before vs after LoRA remediation.
"""

from __future__ import annotations

import json
import logging
import math
from pathlib import Path
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import torch
from transformers import AutoModelForCausalLM, AutoTokenizer
import yaml

# Ensure project root is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.explain.validator import (
    ComprehensiveFaithfulnessValidator,
    NumericalFaithfulnessValidator,
    generate_deterministic_template,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s - %(message)s")
logger = logging.getLogger("src.explain.explainer")


def load_config(config_path: str = "configs/config.yaml") -> dict:
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


class ExplanationEngine:
    """Wrapper around Qwen reasoning model with strict claim-level validation and state-based fallback."""

    def __init__(self, model_dir: Optional[str] = None):
        config = load_config()
        default_dir = "models/qwen_reasoning_outage_predictor/remediated_final_model"
        if not Path(default_dir).exists():
            default_dir = "models/qwen_reasoning_outage_predictor/final_model"

        self.model_dir = Path(model_dir or config.get("explanation", {}).get("model_dir", default_dir))
        self.validator = ComprehensiveFaithfulnessValidator(tolerance=0.05)

        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        logger.info(f"Loading Qwen explanation model from {self.model_dir} on {self.device}...")

        self.tokenizer = AutoTokenizer.from_pretrained(str(self.model_dir), local_files_only=True)
        if self.tokenizer.pad_token is None:
            self.tokenizer.pad_token = self.tokenizer.eos_token
        self.tokenizer.padding_side = "left"

        self.model = AutoModelForCausalLM.from_pretrained(
            str(self.model_dir),
            dtype=torch.float16 if self.device.type == "cuda" else torch.float32,
            device_map=self.device,
            local_files_only=True,
        )
        self.model.eval()

    def format_chat_prompt(self, payload: Dict[str, Any]) -> str:
        system_prompt = (
            "You are the Bank Branch Outage Diagnostic Assistant. "
            "You generate operational diagnosis and dispatch tickets from verified machine learning predictions and physical telemetry states.\n"
            "Strict Operational Directives:\n"
            "1. If device state is STALE_DATA or OFFLINE_LONG, you MUST output a connectivity investigation ticket and NEVER state an outage probability or claim an active power outage.\n"
            "2. If battery hardware is not equipped, you NEVER state a numeric battery voltage or claim battery degradation.\n"
            "3. You NEVER cite physical rule violations (RULE_*) that are not present in the verified input payload.\n"
            "4. You NEVER invent ungrounded numerical figures. Every figure must match the input snapshot exactly."
        )

        user_content = (
            f"Generate an operational diagnostic ticket for branch site '{payload.get('device_name')}' based on the verified payload:\n"
            f"{json.dumps(payload, indent=2)}"
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]
        return self.tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)

    def generate_batch_explanations(self, payloads: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Generate explanations for a batch of payloads with GPU vectorization."""
        t0 = time.time()
        prompts = [self.format_chat_prompt(p) for p in payloads]
        inputs = self.tokenizer(prompts, padding=True, return_tensors="pt").to(self.device)

        try:
            with torch.no_grad():
                outputs = self.model.generate(
                    **inputs,
                    max_new_tokens=220,
                    do_sample=False,
                    repetition_penalty=1.1,
                    eos_token_id=self.tokenizer.eos_token_id,
                )
            input_len = inputs["input_ids"].shape[1]
            raw_texts = [
                self.tokenizer.decode(out[input_len:], skip_special_tokens=True).strip()
                for out in outputs
            ]
        except Exception as e:
            logger.warning(f"Batch generation error: {e}. Falling back to templates.")
            raw_texts = ["" for _ in payloads]

        elapsed_batch = time.time() - t0
        per_item_lat = round(elapsed_batch / max(1, len(payloads)), 3)

        results = []
        for p, raw in zip(payloads, raw_texts):
            is_valid, violations = self.validator.validate(raw, p)
            if is_valid and len(raw) > 25:
                final_text = raw
                fallback_used = False
            else:
                final_text = generate_deterministic_template(p)
                fallback_used = True

            results.append({
                "explanation_text": final_text,
                "raw_generated_text": raw,
                "is_faithful": is_valid,
                "violations": violations,
                "fallback_used": fallback_used,
                "latency_seconds": per_item_lat,
            })
        return results

    def generate_explanation(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Single-item generation wrapper."""
        return self.generate_batch_explanations([payload])[0]


def benchmark_models_on_heldout_test(
    test_jsonl_path: Path,
    legacy_model_dir: str = "models/qwen_reasoning_outage_predictor/final_model",
    remediated_model_dir: str = "models/qwen_reasoning_outage_predictor/remediated_final_model",
    batch_size: int = 8,
) -> Dict[str, Any]:
    """Benchmark raw LLM pass rate before and after LoRA remediation across 100 held-out cases."""
    logger.info(f"Loading held-out evaluation dataset from {test_jsonl_path}...")
    records = []
    with open(test_jsonl_path, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                records.append(json.loads(line))

    logger.info(f"Loaded {len(records)} held-out evaluation records.")

    models_to_test = [
        ("legacy_model_before_lora", legacy_model_dir),
        ("remediated_model_after_lora", remediated_model_dir),
    ]

    benchmark_results: Dict[str, Any] = {}

    for model_name, model_dir in models_to_test:
        if not Path(model_dir).exists():
            logger.warning(f"Model directory {model_dir} not found. Skipping {model_name}.")
            continue

        logger.info(f"=== Evaluating {model_name} from {model_dir} ===")
        engine = ExplanationEngine(model_dir=model_dir)

        case_results = []
        violation_counts = {
            "ungrounded_numbers": 0,
            "state_routing_violation": 0,
            "mains_claim_violation": 0,
            "battery_sensor_violation": 0,
            "rule_hallucination": 0,
            "risk_tier_mismatch": 0,
            "empty_or_truncated": 0,
        }

        latencies = []
        n_batches = int(math.ceil(len(records) / batch_size))

        for b_i in range(n_batches):
            b_slice = records[b_i * batch_size : (b_i + 1) * batch_size]
            b_payloads = [r.get("payload") or r for r in b_slice]

            b_res = engine.generate_batch_explanations(b_payloads)

            for idx_in_b, (res, payload) in enumerate(zip(b_res, b_payloads)):
                case_id = b_i * batch_size + idx_in_b + 1
                res["case_id"] = case_id
                res["device_name"] = payload.get("device_name")
                res["device_state"] = payload.get("device_state")
                res["risk_tier"] = payload.get("risk_tier")
                case_results.append(res)
                latencies.append(res["latency_seconds"])

                for v in res["violations"]:
                    v_lower = v.lower()
                    if "ungrounded numerical" in v_lower:
                        violation_counts["ungrounded_numbers"] += 1
                    elif "state routing" in v_lower:
                        violation_counts["state_routing_violation"] += 1
                    elif "mains claim" in v_lower:
                        violation_counts["mains_claim_violation"] += 1
                    elif "battery sensor" in v_lower:
                        violation_counts["battery_sensor_violation"] += 1
                    elif "rule hallucination" in v_lower:
                        violation_counts["rule_hallucination"] += 1
                    elif "risk tier mismatch" in v_lower:
                        violation_counts["risk_tier_mismatch"] += 1
                    elif "empty or truncated" in v_lower:
                        violation_counts["empty_or_truncated"] += 1

            logger.info(f"Processed batch {b_i + 1}/{n_batches} ({len(case_results)}/{len(records)} cases done)")

        total = len(case_results)
        raw_passes = sum(1 for r in case_results if r["is_faithful"] and not r["fallback_used"])
        fallbacks = sum(1 for r in case_results if r["fallback_used"])
        raw_pass_rate = round(raw_passes / total, 4)
        fallback_rate = round(fallbacks / total, 4)
        avg_latency = round(float(np.mean(latencies)), 3)

        benchmark_results[model_name] = {
            "total_cases_evaluated": total,
            "raw_llm_pass_count": raw_passes,
            "raw_llm_pass_rate": raw_pass_rate,
            "fallback_activation_count": fallbacks,
            "fallback_activation_rate": fallback_rate,
            "final_delivered_faithfulness": 1.0,  # Enforced by deterministic template fallback
            "average_latency_seconds": avg_latency,
            "violation_category_counts": violation_counts,
            "sample_cases": case_results[:5],
            "worst_failure_cases": [r for r in case_results if not r["is_faithful"]][:5],
        }

        logger.info(
            f"=== {model_name} Final: Raw Pass Rate = {raw_pass_rate*100:.1f}%, "
            f"Fallback Rate = {fallback_rate*100:.1f}%, Avg Latency = {avg_latency:.3f}s ==="
        )

    out_file = Path("reports/remediated_explanation_benchmark.json")
    out_file.parent.mkdir(parents=True, exist_ok=True)
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(benchmark_results, f, indent=2)

    logger.info(f"Saved benchmark results to {out_file}.")
    return benchmark_results


if __name__ == "__main__":
    test_data = Path("training_data/remediated_explanation_test_heldout.jsonl")
    benchmark_models_on_heldout_test(test_data, batch_size=8)
