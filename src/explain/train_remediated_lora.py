"""src/explain/train_remediated_lora.py
Fine-tune Qwen reasoning model on remediated explanation training dataset.

Base model: Qwen/Qwen2.5-0.5B-Instruct
Technique: LoRA PEFT with assistant-only loss masking
Compute: NVIDIA RTX 3050 Laptop GPU (CUDA fp16) or CPU fallback
Outputs:
  - models/qwen_reasoning_outage_predictor/remediated_lora_adapter/
  - models/qwen_reasoning_outage_predictor/remediated_final_model/
"""

from __future__ import annotations

import json
import logging
import math
import os
from pathlib import Path
import time
from typing import List

import torch
from torch.utils.data import DataLoader, Dataset
from transformers import AutoModelForCausalLM, AutoTokenizer, get_cosine_schedule_with_warmup
from peft import LoraConfig, TaskType, get_peft_model

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s - %(message)s")
logger = logging.getLogger("src.explain.train_lora")

ROOT = Path(__file__).resolve().parents[2]
TRAIN_DATA = ROOT / "training_data/remediated_explanation_train.jsonl"
OUT_DIR = ROOT / "models/qwen_reasoning_outage_predictor"
LORA_DIR = OUT_DIR / "remediated_lora_adapter"
FINAL_DIR = OUT_DIR / "remediated_final_model"
EVAL_FILE = OUT_DIR / "remediated_training_summary.json"

MODEL_ID = "Qwen/Qwen2.5-0.5B-Instruct"
MAX_LENGTH = 1024
BATCH_SIZE = 2
GRAD_ACCUM_STEPS = 4
EPOCHS = 3
LEARNING_RATE = 2e-4
WEIGHT_DECAY = 0.01


class RemediatedExplanationDataset(Dataset):
    def __init__(self, jsonl_path: Path, tokenizer: AutoTokenizer, max_length: int = 1024):
        self.examples = []
        with open(jsonl_path, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                rec = json.loads(line)
                msgs = rec.get("messages", [])
                if len(msgs) >= 3:
                    self.examples.append(msgs)

        self.tokenizer = tokenizer
        self.max_length = max_length
        self.encoded = self._preprocess()

    def _preprocess(self):
        encoded_data = []
        for msgs in self.examples:
            full_text = self.tokenizer.apply_chat_template(msgs, tokenize=False)
            prompt_msgs = [m for m in msgs if m["role"] in ("system", "user")]
            prompt_text = self.tokenizer.apply_chat_template(prompt_msgs, tokenize=False, add_generation_prompt=True)

            enc_full = self.tokenizer(
                full_text,
                truncation=True,
                max_length=self.max_length,
                padding=False,
                return_tensors=None,
            )
            enc_prompt = self.tokenizer(
                prompt_text,
                truncation=True,
                max_length=self.max_length,
                padding=False,
                return_tensors=None,
            )

            input_ids = enc_full["input_ids"]
            attention_mask = enc_full["attention_mask"]
            prompt_len = len(enc_prompt["input_ids"])

            labels = list(input_ids)
            for idx in range(min(prompt_len, len(labels))):
                labels[idx] = -100

            encoded_data.append({
                "input_ids": input_ids,
                "attention_mask": attention_mask,
                "labels": labels,
            })
        return encoded_data

    def __len__(self):
        return len(self.encoded)

    def __getitem__(self, idx):
        return self.encoded[idx]


def collate_fn(batch, pad_token_id: int):
    max_len = max(len(item["input_ids"]) for item in batch)
    input_ids_list = []
    attention_mask_list = []
    labels_list = []

    for item in batch:
        cur_len = len(item["input_ids"])
        pad_len = max_len - cur_len
        input_ids_list.append(item["input_ids"] + [pad_token_id] * pad_len)
        attention_mask_list.append(item["attention_mask"] + [0] * pad_len)
        labels_list.append(item["labels"] + [-100] * pad_len)

    return {
        "input_ids": torch.tensor(input_ids_list, dtype=torch.long),
        "attention_mask": torch.tensor(attention_mask_list, dtype=torch.long),
        "labels": torch.tensor(labels_list, dtype=torch.long),
    }


def evaluate(model, val_loader, device):
    model.eval()
    total_loss = 0.0
    total_tokens = 0

    with torch.no_grad():
        for batch in val_loader:
            input_ids = batch["input_ids"].to(device)
            attention_mask = batch["attention_mask"].to(device)
            labels = batch["labels"].to(device)

            outputs = model(
                input_ids=input_ids,
                attention_mask=attention_mask,
                labels=labels,
            )
            loss = outputs.loss
            valid_tokens = (labels != -100).sum().item()
            if valid_tokens > 0:
                total_loss += loss.item() * valid_tokens
                total_tokens += valid_tokens

    avg_loss = total_loss / max(1, total_tokens)
    perplexity = math.exp(min(15.0, avg_loss))
    return avg_loss, perplexity


def run_training():
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    logger.info(f"Target compute device: {device} ({torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'CPU'})")

    LORA_DIR.mkdir(parents=True, exist_ok=True)
    FINAL_DIR.mkdir(parents=True, exist_ok=True)

    logger.info(f"Loading tokenizer: {MODEL_ID}...")
    tokenizer = AutoTokenizer.from_pretrained(MODEL_ID, use_fast=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    logger.info(f"Loading remediated explanation training dataset from {TRAIN_DATA}...")
    full_dataset = RemediatedExplanationDataset(TRAIN_DATA, tokenizer, max_length=MAX_LENGTH)
    n_total = len(full_dataset)
    n_val = min(50, int(n_total * 0.1))
    n_train = n_total - n_val

    torch.manual_seed(42)
    train_dataset, val_dataset = torch.utils.data.random_split(full_dataset, [n_train, n_val])
    logger.info(f"Dataset split: {len(train_dataset)} training dialogues, {len(val_dataset)} validation dialogues.")

    train_loader = DataLoader(
        train_dataset,
        batch_size=BATCH_SIZE,
        shuffle=True,
        collate_fn=lambda b: collate_fn(b, tokenizer.pad_token_id),
    )
    val_loader = DataLoader(
        val_dataset,
        batch_size=BATCH_SIZE,
        shuffle=False,
        collate_fn=lambda b: collate_fn(b, tokenizer.pad_token_id),
    )

    logger.info(f"Loading base model: {MODEL_ID} in fp16...")
    base_model = AutoModelForCausalLM.from_pretrained(
        MODEL_ID,
        dtype=torch.float16 if device.type == "cuda" else torch.float32,
        device_map=device,
    )

    logger.info("Configuring LoRA adapters...")
    lora_config = LoraConfig(
        task_type=TaskType.CAUSAL_LM,
        r=16,
        lora_alpha=32,
        lora_dropout=0.05,
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
        bias="none",
    )
    model = get_peft_model(base_model, lora_config)
    model.print_trainable_parameters()

    total_steps = (len(train_loader) // GRAD_ACCUM_STEPS) * EPOCHS
    warmup_steps = int(total_steps * 0.1)
    optimizer = torch.optim.AdamW(model.parameters(), lr=LEARNING_RATE, weight_decay=WEIGHT_DECAY)
    scheduler = get_cosine_schedule_with_warmup(optimizer, num_warmup_steps=warmup_steps, num_training_steps=total_steps)

    logger.info(f"Starting LoRA fine-tuning: {EPOCHS} epochs, {total_steps} optimizer steps...")
    start_time = time.time()
    step_count = 0

    init_val_loss, init_ppl = evaluate(model, val_loader, device)
    logger.info(f"Pre-training baseline validation loss: {init_val_loss:.4f} | Perplexity: {init_ppl:.2f}")

    train_losses = []
    for epoch in range(1, EPOCHS + 1):
        model.train()
        epoch_loss = 0.0
        accum_loss = 0.0

        for b_idx, batch in enumerate(train_loader):
            input_ids = batch["input_ids"].to(device)
            attention_mask = batch["attention_mask"].to(device)
            labels = batch["labels"].to(device)

            outputs = model(
                input_ids=input_ids,
                attention_mask=attention_mask,
                labels=labels,
            )
            loss = outputs.loss / GRAD_ACCUM_STEPS
            loss.backward()

            accum_loss += loss.item()
            epoch_loss += loss.item() * GRAD_ACCUM_STEPS

            if (b_idx + 1) % GRAD_ACCUM_STEPS == 0 or (b_idx + 1) == len(train_loader):
                torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
                optimizer.step()
                scheduler.step()
                optimizer.zero_grad()
                step_count += 1

                if step_count % 15 == 0:
                    lr_curr = scheduler.get_last_lr()[0]
                    logger.info(f"Epoch {epoch}/{EPOCHS} | Step {step_count}/{total_steps} | Loss: {accum_loss:.4f} | LR: {lr_curr:.2e}")
                accum_loss = 0.0

        avg_epoch_loss = epoch_loss / len(train_loader)
        train_losses.append(avg_epoch_loss)

        val_loss, val_ppl = evaluate(model, val_loader, device)
        logger.info(f"--- Epoch {epoch} Complete --- Train Loss: {avg_epoch_loss:.4f} | Val Loss: {val_loss:.4f} | Val Perplexity: {val_ppl:.2f}")

    elapsed = time.time() - start_time
    logger.info(f"LoRA fine-tuning completed in {elapsed:.1f} s ({elapsed / 60:.2f} min).")

    logger.info(f"Saving remediated LoRA adapter to {LORA_DIR}...")
    model.save_pretrained(str(LORA_DIR))
    tokenizer.save_pretrained(str(LORA_DIR))

    logger.info("Merging LoRA weights with base model for deployment...")
    merged_model = model.merge_and_unload()
    logger.info(f"Saving merged standalone model to {FINAL_DIR}...")
    merged_model.save_pretrained(str(FINAL_DIR), safe_serialization=True)
    tokenizer.save_pretrained(str(FINAL_DIR))

    summary = {
        "model_id": MODEL_ID,
        "epochs": EPOCHS,
        "train_samples": len(train_dataset),
        "val_samples": len(val_dataset),
        "elapsed_seconds": round(elapsed, 1),
        "initial_val_loss": round(init_val_loss, 4),
        "final_val_loss": round(val_loss, 4),
        "initial_perplexity": round(init_ppl, 2),
        "final_perplexity": round(val_ppl, 2),
        "final_train_loss": round(train_losses[-1], 4),
    }

    with open(EVAL_FILE, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    logger.info(f"Saved training summary to {EVAL_FILE}.")
    return summary


if __name__ == "__main__":
    run_training()
