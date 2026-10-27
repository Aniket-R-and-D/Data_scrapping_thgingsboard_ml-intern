#!/usr/bin/env python
"""Run inference with the fine-tuned ThingsBoard causal reasoning outage prediction model.

Usage:
    python run_reasoning_inference.py --device BOI-MALDATOWN
    python run_reasoning_inference.py --device BOI-BAGULA
    python run_reasoning_inference.py --interactive
"""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
import sys

# Ensure UTF-8 output on Windows consoles
if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr.encoding != "utf-8":
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent
MODEL_PATH = ROOT / "models/qwen_reasoning_outage_predictor/final_model"
DATASET_PATH = ROOT / "training_data/mcp_fleet_dataset.jsonl"


def load_model(model_dir: Path):
    logger.info(f"Loading reasoning model from {model_dir}...")
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    tokenizer = AutoTokenizer.from_pretrained(str(model_dir), use_fast=True)
    model = AutoModelForCausalLM.from_pretrained(
        str(model_dir),
        dtype=torch.float16 if device.type == "cuda" else torch.float32,
        device_map=device,
    )
    return model, tokenizer, device


def get_device_prompt(device_name_or_id: str) -> tuple[str, str]:
    if not DATASET_PATH.exists():
        raise FileNotFoundError(f"Dataset not found at {DATASET_PATH}")

    with open(DATASET_PATH, "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            rec = json.loads(line)
            dname = rec.get("device_name", "")
            did = rec.get("device_id", "")
            if device_name_or_id.lower() in dname.lower() or device_name_or_id.lower() in did.lower():
                msgs = rec.get("messages", [])
                system_prompt = msgs[0]["content"] if msgs else "You are the Antigravity ThingsBoard Fleet Intelligence AI."
                user_prompt = msgs[1]["content"] if len(msgs) > 1 else f"Analyze telemetry for {dname}."
                return system_prompt, user_prompt

    # Fallback generic prompt if device not in dataset
    system_prompt = "You are the Antigravity ThingsBoard Fleet Intelligence AI."
    user_prompt = (
        f"Perform a 24-hour predictive outage and risk assessment for branch device '{device_name_or_id}'.\n"
        "Observed Telemetry Snapshot:\n"
        "- AC Line Voltage: 224.5 V\n"
        "- Battery Bus Voltage: 11.1 V (State of Charge: 22%)\n"
        "- System Draw: 1.85 A\n"
        "- Primary Power State: MAINS DISCONNECTED / POWER LOSS\n"
        "- Surveillance NVR Status: OFFLINE (CCTV Stream Interrupted)\n"
        "- Fire Alarm System (FAS): NORMAL (Supervisory Clear)\n"
        "- Storage Health: HEALTHY (SMART Verified)\n"
        "- Alarm Profile: 12 Critical, 4 Major, 0 Warning, 1 Active Unacknowledged.\n\n"
        "Provide your step-by-step causal reasoning, root cause analysis, 24-hour outage prediction, and recommended dispatch actions."
    )
    return system_prompt, user_prompt


def run_prediction(model, tokenizer, device, system_prompt: str, user_prompt: str) -> str:
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    chat_prompt = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    inputs = tokenizer(chat_prompt, return_tensors="pt").to(device)

    with torch.no_grad():
        gen_tokens = model.generate(
            **inputs,
            max_new_tokens=500,
            temperature=0.3,
            top_p=0.9,
            repetition_penalty=1.1,
            do_sample=True,
            eos_token_id=tokenizer.eos_token_id,
        )

    output = tokenizer.decode(gen_tokens[0][inputs["input_ids"].shape[1]:], skip_special_tokens=True)
    return output


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--device", default="BOI-MALDATOWN", help="Device name or UUID to test")
    p.add_argument("--model-path", default=str(MODEL_PATH), type=Path)
    args = p.parse_args()

    model, tokenizer, dev = load_model(args.model_path)
    system_prompt, user_prompt = get_device_prompt(args.device)

    print("\n" + "=" * 60)
    print("DEVICE INQUIRY PROMPT:")
    print("=" * 60)
    print(user_prompt)
    print("\n" + "=" * 60)
    print("MODEL CAUSAL REASONING & 24H OUTAGE PREDICTION:")
    print("=" * 60)

    prediction = run_prediction(model, tokenizer, dev, system_prompt, user_prompt)
    print(prediction)
    print("=" * 60 + "\n")


if __name__ == "__main__":
    main()
