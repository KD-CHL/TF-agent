# -*- coding: utf-8 -*-
"""QLoRA 训练 CLI（开发工具，不参与运行时 Agent 后端回退）。"""
from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List


@dataclass(frozen=True)
class TrainingConfig:
    model_path: str
    data_path: str
    output_dir: str
    max_length: int = 1024
    epochs: int = 5
    batch_size: int = 2
    grad_accumulation: int = 8
    learning_rate: float = 2e-4


def validate_training_data(data_path: str, *, max_errors: int = 10) -> List[str]:
    """校验 JSONL 训练数据 schema，不加载模型、不联网。"""
    errors: List[str] = []
    if not data_path or not os.path.isfile(data_path):
        return ["训练数据文件不存在。"]
    try:
        with open(data_path, "r", encoding="utf-8") as handle:
            for line_no, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    errors.append(f"第 {line_no} 行不是合法 JSON。")
                    continue
                if not isinstance(row, dict):
                    errors.append(f"第 {line_no} 行必须是 JSON 对象。")
                    continue
                if not isinstance(row.get("instruction"), str) or not row["instruction"].strip():
                    errors.append(f"第 {line_no} 行缺少非空 instruction。")
                if not isinstance(row.get("output"), str) or not row["output"].strip():
                    errors.append(f"第 {line_no} 行缺少非空 output。")
                if len(errors) >= max_errors:
                    break
    except OSError:
        return ["训练数据无法读取。"]
    return errors


def format_chatml(example: Dict[str, Any]) -> Dict[str, str]:
    instruction = str(example["instruction"])
    output = str(example["output"])
    return {
        "text": (
            "<|im_start|>system\n你是一个专业的遥感算法与文献检索智能体。<|im_end|>\n"
            f"<|im_start|>user\n{instruction}<|im_end|>\n"
            f"<|im_start|>assistant\n{output}<|im_end|>"
        )
    }


def run_training(config: TrainingConfig) -> str:
    """按配置执行训练；调用前已完成 schema 校验。"""
    errors = validate_training_data(config.data_path)
    if errors:
        raise ValueError("训练数据校验失败：" + "；".join(errors))
    if not config.model_path:
        raise ValueError("必须提供 model_path。")
    if config.max_length <= 0 or config.epochs <= 0:
        raise ValueError("max_length 与 epochs 必须为正数。")

    # 重型依赖只在显式 run_training 时导入，避免影响 Agent/Streamlit 启动。
    import torch
    from datasets import load_dataset
    from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
    from transformers import (
        AutoModelForCausalLM,
        AutoTokenizer,
        BitsAndBytesConfig,
    )
    from trl import SFTConfig, SFTTrainer

    bnb_config = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_use_double_quant=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_compute_dtype=torch.bfloat16,
    )
    tokenizer = AutoTokenizer.from_pretrained(config.model_path, trust_remote_code=True)
    tokenizer.pad_token = tokenizer.eos_token
    model = AutoModelForCausalLM.from_pretrained(
        config.model_path,
        quantization_config=bnb_config,
        device_map=os.environ.get("CSTF_TRAIN_DEVICE_MAP", "auto"),
        trust_remote_code=True,
    )
    model.gradient_checkpointing_enable()
    model = prepare_model_for_kbit_training(model)
    peft_config = LoraConfig(
        r=16,
        lora_alpha=32,
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
        lora_dropout=0.05,
        bias="none",
        task_type="CAUSAL_LM",
    )
    model = get_peft_model(model, peft_config)
    dataset = load_dataset("json", data_files=config.data_path, split="train")
    formatted_dataset = dataset.map(format_chatml)
    training_args = SFTConfig(
        output_dir=config.output_dir,
        dataset_text_field="text",
        max_length=config.max_length,
        per_device_train_batch_size=config.batch_size,
        gradient_accumulation_steps=config.grad_accumulation,
        learning_rate=config.learning_rate,
        logging_steps=1,
        num_train_epochs=config.epochs,
        optim="paged_adamw_8bit",
        bf16=True,
        save_strategy="epoch",
        lr_scheduler_type="cosine",
        max_grad_norm=1.0,
        report_to="none",
    )
    trainer = SFTTrainer(
        model=model,
        train_dataset=formatted_dataset,
        processing_class=tokenizer,
        args=training_args,
    )
    trainer.train()
    checkpoint = os.path.join(config.output_dir, "final_checkpoint")
    trainer.model.save_pretrained(checkpoint)
    tokenizer.save_pretrained(checkpoint)
    return checkpoint


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="CSTF QLoRA training tool")
    parser.add_argument("--model-path", default=os.environ.get("CSTF_TRAIN_MODEL_PATH", ""))
    parser.add_argument("--data-path", default=os.environ.get("CSTF_TRAIN_DATA_PATH", ""))
    parser.add_argument("--output-dir", default=os.environ.get("CSTF_TRAIN_OUTPUT_DIR", "./qwen_rs_agent_lora"))
    parser.add_argument("--max-length", type=int, default=1024)
    parser.add_argument("--epochs", type=int, default=5)
    parser.add_argument("--batch-size", type=int, default=2)
    parser.add_argument("--grad-accumulation", type=int, default=8)
    parser.add_argument("--learning-rate", type=float, default=2e-4)
    parser.add_argument("--dry-run", action="store_true", help="只校验配置与训练数据，不加载模型")
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    config = TrainingConfig(
        model_path=args.model_path,
        data_path=args.data_path,
        output_dir=args.output_dir,
        max_length=args.max_length,
        epochs=args.epochs,
        batch_size=args.batch_size,
        grad_accumulation=args.grad_accumulation,
        learning_rate=args.learning_rate,
    )
    errors = validate_training_data(config.data_path)
    if errors:
        for error in errors:
            print(error)
        return 2
    if args.dry_run:
        print("训练配置与数据 schema 校验通过。")
        return 0
    print(f"开始训练：model={config.model_path!r} data={config.data_path!r}")
    checkpoint = run_training(config)
    print(f"训练完成：{checkpoint}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
