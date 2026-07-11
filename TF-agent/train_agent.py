import os
import torch
from datasets import load_dataset
from transformers import (
    AutoModelForCausalLM,
    AutoTokenizer,
    BitsAndBytesConfig,
    TrainingArguments
)
from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
from trl import SFTTrainer,SFTConfig

# ==========================================
# 1. 核心参数与路径配置
# ==========================================
# 替换为你用 ModelScope 下载在本地的真实 Qwen2.5 模型文件夹路径
# 如果没下到本地，可以直接填 "Qwen/Qwen2.5-7B-Instruct"，它会自动联网下
model_path = "E:\Code\GEE\Qwen2.5-7B-Instruct"    
data_path = r"E:\Code\GEE\remote_sensing_rag_sft.jsonl"  # 你的遥感数据
output_dir = "./qwen_rs_agent_lora"         # 训练完权重的保存位置

print("🚀 正在点火：初始化 RTX 5080 炼丹炉...")

# ==========================================
# 2. 极致显存压缩：4-bit 量化配置 (QLoRA)
# ==========================================
# 把 14GB 的 7B 模型强行压缩到 5GB 左右，让 16G 的 5080 跑得游刃有余
bnb_config = BitsAndBytesConfig(
    load_in_4bit=True,
    bnb_4bit_use_double_quant=True,
    bnb_4bit_quant_type="nf4",
    bnb_4bit_compute_dtype=torch.bfloat16 # RTX 5080 专属红利：高精度 bf16，防止梯度爆炸
)

# ==========================================
# 3. 加载 Tokenizer 与底座模型
# ==========================================
print("📦 正在把底座模型塞进显卡...")
tokenizer = AutoTokenizer.from_pretrained(model_path, trust_remote_code=True)
tokenizer.pad_token = tokenizer.eos_token # 指定填充符

model = AutoModelForCausalLM.from_pretrained(
    model_path,
    quantization_config=bnb_config,
    device_map="auto", # 自动把计算图扔给你的 5080
    trust_remote_code=True
)

# 开启梯度检查点 (以时间换空间，省显存的终极杀器)
model.gradient_checkpointing_enable()
model = prepare_model_for_kbit_training(model)

# ==========================================
# 4. 注入灵魂：LoRA 适配器配置
# ==========================================
# 我们不更新底座的百亿参数，只训练外挂的 LoRA 权重层
peft_config = LoraConfig(
    r=16,               # 秩大小：决定模型能学到多少新知识
    lora_alpha=32,      # 缩放系数
    target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"], # 对所有线性层开刀，效果最好
    lora_dropout=0.05,
    bias="none",
    task_type="CAUSAL_LM"
)
model = get_peft_model(model, peft_config)
model.print_trainable_parameters() # 打印参数：你会看到实际只训练了总参数的百分之零点几！

# ==========================================
# 5. 数据洗牌：对齐 Qwen2.5 的 ChatML 格式
# ==========================================
dataset = load_dataset("json", data_files=data_path, split="train")

def format_chatml(example):
    """
    这一步极其关键！大模型极其讲究规矩。
    我们要把你 JSONL 里的 instruction 和 output，包裹进 Qwen 专属的 <|im_start|> 和 <|im_end|> 标签里。
    """
    instruction = example['instruction']
    output = example['output']
    
    # 拼接标准的 Qwen 训练格式
    text = (
        "<|im_start|>system\n你是一个专业的遥感算法与文献检索智能体。<|im_end|>\n"
        f"<|im_start|>user\n{instruction}<|im_end|>\n"
        f"<|im_start|>assistant\n{output}<|im_end|>"
    )
    return {"text": text}

formatted_dataset = dataset.map(format_chatml)

# ==========================================
# 6. 定义训练策略与引擎 (适配最新版 TRL API)
# ==========================================
training_args = SFTConfig(
    output_dir=output_dir,
    dataset_text_field="text",      # <-- 被官方挪到这里了！
    max_length=1024,            # <-- 被官方挪到这里了！
    per_device_train_batch_size=2,  
    gradient_accumulation_steps=8,  
    learning_rate=2e-4,             
    logging_steps=1,                # <-- 改成 1，让你步步都能看到 Loss 狂降！
    num_train_epochs=5,             
    optim="paged_adamw_8bit",       
    bf16=True,                      
    save_strategy="epoch",          
    lr_scheduler_type="cosine",     
    max_grad_norm=1.0,
    report_to="none"                
)

trainer = SFTTrainer(
    model=model,
    train_dataset=formatted_dataset,
    # peft_config=peft_config,
    processing_class=tokenizer,
    args=training_args,             # <-- 现在所有的配置都打包在 args 里了
)

# ==========================================
# 7. 点火！
# ==========================================
print("🔥 训练引擎已启动，尽情燃烧 5080 吧！")
trainer.train()

# 保存最终的 LoRA 权重
trainer.model.save_pretrained(f"{output_dir}/final_checkpoint")
tokenizer.save_pretrained(f"{output_dir}/final_checkpoint")
print(f"🎉 完美收官！LoRA 权重已保存至：{output_dir}/final_checkpoint")