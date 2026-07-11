from fastapi import FastAPI
from pydantic import BaseModel
import torch
from transformers import AutoModelForCausalLM, AutoTokenizer
import uvicorn

app = FastAPI()

# ==========================================
# 1. 唤醒融合后的模型
# ==========================================
print("🚀 正在将 Qwen_Agent_Merged 装载到 RTX 5080...")
model_path = "./Qwen_Agent_Merged"  # 你的融合模型文件夹
tokenizer = AutoTokenizer.from_pretrained(model_path)
model = AutoModelForCausalLM.from_pretrained(
    model_path, 
    torch_dtype=torch.bfloat16, 
    device_map="auto"
)
print("✅ 模型装载完毕！API 服务器准备就绪。")

# ==========================================
# 2. 定义 OpenAI 兼容的数据结构
# ==========================================
class ChatRequest(BaseModel):
    model: str
    messages: list
    temperature: float = 0.1

# ==========================================
# 3. 核心路由：伪装成 OpenAI 的 /v1/chat/completions
# ==========================================
@app.post("/v1/chat/completions")
async def chat(request: ChatRequest):
    # 严格按照 Qwen 的 ChatML 格式拼接对话
    text = tokenizer.apply_chat_template(request.messages, tokenize=False, add_generation_prompt=True)
    inputs = tokenizer([text], return_tensors="pt").to(model.device)

    # 发起推理
    outputs = model.generate(**inputs, max_new_tokens=512, temperature=request.temperature)
    response_text = tokenizer.decode(outputs[0][inputs.input_ids.shape[1]:], skip_special_tokens=True)

    print(f"\n[Agent 大脑输出] -> {response_text}\n")

    # 返回极其标准的 OpenAI JSON 格式
    return {
        "id": "chatcmpl-5080-custom",
        "object": "chat.completion",
        "model": request.model,
        "choices": [{
            "index": 0,
            "message": {
                "role": "assistant",
                "content": response_text,
            },
            "finish_reason": "stop"
        }]
    }

if __name__ == "__main__":
    # 在本地 8000 端口启动服务
    uvicorn.run(app, host="0.0.0.0", port=8000)