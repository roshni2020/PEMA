"""SentinelQ NPU server - runs on the Snapdragon X Elite laptop.

An OpenAI-compatible HTTP server built directly on the Qualcomm GenieX Python SDK, so the
model runs on the Hexagon NPU (device_map="npu") and every response carries the GenieX
profile: device, backend, prefill/decode speed, time-to-first-token. The UNO Q agent shows
those numbers on the dashboard, which is how we make NPU usage visible to the judges.

Why not only `geniex serve`? You can use it (same API on port 18181). This server adds
/stats and per-response profile data, and supports a VLM for camera-frame questions.

Run:
    python npu_server.py                       # LLM on NPU, port 18182
    python npu_server.py --vlm                 # also load a VLM for image questions
    python npu_server.py --model ai-hub-models/Qwen3-4B       # QAIRT NPU bundle
    python npu_server.py --device llama_cpp:CPU                # CPU baseline for comparison

Then on the UNO Q:  GENIEX_URL=http://<laptop-ip>:18182/v1
"""
import argparse
import base64
import os
import re
import tempfile
import threading
import time
import uuid

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import uvicorn

from geniex import AutoModelForCausalLM, AutoModelForVision2Seq

ap = argparse.ArgumentParser()
# Tested on Snapdragon X Elite: bartowski/Qwen_Qwen3.5-0.8B-GGUF Q4_0 on HTP0 -> 22.6 tok/s decode.
# unsloth/Qwen3.5-2B-GGUF has Q4_0 + mmproj, so one model serves both text and camera frames.
ap.add_argument("--model", default=os.getenv("SQ_LLM", "unsloth/Qwen3.5-2B-GGUF"))
ap.add_argument("--precision", default=os.getenv("SQ_PRECISION", "Q4_0"))
# GenieX device maps on Snapdragon X Elite:
#   GGUF models  -> llama.cpp runtime: "llama_cpp:HTP0" (Hexagon NPU), "llama_cpp:GPUOpenCL", "llama_cpp:CPU"
#   ai-hub-models -> QAIRT runtime:    "qairt" (Hexagon NPU, pre-quantized bundles)
# Plain "npu" resolves to qairt, so for GGUF use llama_cpp:HTP0.
ap.add_argument("--device", default=os.getenv("SQ_DEVICE", "llama_cpp:HTP0"))
ap.add_argument("--vlm", action="store_true", help="also load a vision model for image inputs")
ap.add_argument("--vlm-model", default=os.getenv("SQ_VLM", ""), help="defaults to --model (Qwen3.5 is multimodal)")
ap.add_argument("--host", default="0.0.0.0")
ap.add_argument("--port", type=int, default=18182)
args = ap.parse_args()
if not args.vlm_model:
    args.vlm_model = args.model

app = FastAPI(title="SentinelQ GenieX NPU server")
_lock = threading.Lock()
stats = {"requests": 0, "last": None, "device": args.device, "llm": args.model,
         "vlm": args.vlm_model if args.vlm else None, "started": time.time()}

print(f"[npu] loading {args.model} ({args.precision}) on {args.device} ...", flush=True)
t0 = time.time()
def _device_for(model_id: str) -> str:
    return "qairt" if model_id.startswith("ai-hub-models/") else args.device


kw = {"device_map": _device_for(args.model)}
if not args.model.startswith("ai-hub-models/"):
    kw["precision"] = args.precision

vlm = None
if args.vlm and args.vlm_model == args.model:
    # one multimodal model (e.g. Qwen3.5) handles text and images; load it once
    llm = vlm = AutoModelForVision2Seq.from_pretrained(args.model, **kw)
    print(f"[npu] multimodal model ready in {time.time()-t0:.1f}s  capabilities={vlm.capabilities()}", flush=True)
else:
    llm = AutoModelForCausalLM.from_pretrained(args.model, **kw)
    print(f"[npu] LLM ready in {time.time()-t0:.1f}s", flush=True)

if args.vlm and vlm is None:
    t0 = time.time()
    print(f"[npu] loading VLM {args.vlm_model} on {args.device} ...", flush=True)
    vkw = {"device_map": _device_for(args.vlm_model)}
    if not args.vlm_model.startswith("ai-hub-models/"):
        vkw["precision"] = args.precision
    vlm = AutoModelForVision2Seq.from_pretrained(args.vlm_model, **vkw)
    print(f"[npu] VLM ready in {time.time()-t0:.1f}s  capabilities={vlm.capabilities()}", flush=True)


class ChatRequest(BaseModel):
    model: str | None = None
    messages: list
    max_tokens: int | None = 256
    temperature: float | None = 0.2
    stream: bool | None = False
    response_format: dict | None = None     # {"type": "json_object"} -> grammar-constrained decoding
    grammar: str | None = None              # custom llama.cpp GBNF grammar (overrides response_format)


# llama.cpp GBNF grammar for JSON (from llama.cpp/grammars/json.gbnf). With this, the model
# physically cannot emit a stray quote or brace: every token is checked against the grammar.
JSON_GBNF = r'''
root   ::= object
value  ::= object | array | string | number | ("true" | "false" | "null") ws
object ::= "{" ws ( string ":" ws value ("," ws string ":" ws value)* )? "}" ws
array  ::= "[" ws ( value ("," ws value)* )? "]" ws
string ::= "\"" ( [^"\\\x7F\x00-\x1F] | "\\" (["\\bfnrt] | "u" [0-9a-fA-F]{4}) )* "\"" ws
number ::= ("-"? ([0-9] | [1-9] [0-9]{0,15})) ("." [0-9]+)? ([eE] [-+]? [0-9] [1-9]{0,15})? ws
ws     ::= | " " | "\n" [ \t]{0,20}
'''


def _split_content(content):
    """OpenAI content can be a string or a list of {type:text|image_url} parts."""
    if isinstance(content, str):
        return content, []
    text, images = [], []
    for part in content or []:
        if part.get("type") == "text":
            text.append(part.get("text", ""))
        elif part.get("type") == "image_url":
            url = part["image_url"]["url"] if isinstance(part.get("image_url"), dict) else part.get("image_url")
            m = re.match(r"data:image/(\w+);base64,(.*)", url or "", flags=re.S)
            if m:
                path = os.path.join(tempfile.gettempdir(), f"sq_{uuid.uuid4().hex}.{m.group(1)}")
                with open(path, "wb") as f:
                    f.write(base64.b64decode(m.group(2)))
                images.append(path)
            elif url and os.path.exists(url):
                images.append(url)
    return "\n".join(text), images


def _profile_dict(p):
    keys = ("device", "backend", "quant", "prompt_tokens", "generated_tokens",
            "prefill_speed", "decode_speed", "ttft", "prompt_time", "decode_time", "stop_reason")
    out = {}
    for k in keys:
        v = getattr(p, k, None)
        if k in ("ttft", "prompt_time", "decode_time") and isinstance(v, (int, float)):
            v = v / 1e6          # GenieX reports microseconds; the dashboard shows seconds
        out[k] = round(v, 2) if isinstance(v, float) else v
    return out


@app.get("/v1/models")
def models():
    data = [{"id": args.model, "object": "model", "owned_by": "geniex", "device": args.device,
             "vision": vlm is not None}]
    if vlm and args.vlm_model != args.model:
        data.append({"id": args.vlm_model, "object": "model", "owned_by": "geniex", "device": args.device})
    return {"object": "list", "data": data}


@app.get("/stats")
def get_stats():
    return stats


@app.post("/v1/chat/completions")
def chat(req: ChatRequest):
    # Flatten messages; collect images from any message
    messages, images = [], []
    for m in req.messages:
        text, imgs = _split_content(m.get("content"))
        images += imgs
        messages.append({"role": m.get("role", "user"), "content": text})

    use_vision = bool(images) and vlm is not None
    model = vlm if use_vision else llm
    model_id = args.vlm_model if use_vision else args.model

    if use_vision:
        # GenieX VLM format: image parts live inside the last user message, and the same
        # paths are passed to generate(images=...). See geniex python quickstart "VLM inference".
        last = messages[-1]
        last["content"] = [{"type": "image", "image": p} for p in images] + \
                          [{"type": "text", "text": last["content"]}]

    gen_kw = {"max_new_tokens": req.max_tokens or 256, "temperature": req.temperature or 0.0}
    if req.grammar:
        gen_kw["grammar"] = req.grammar
    elif req.response_format and req.response_format.get("type") == "json_object":
        gen_kw["grammar"] = JSON_GBNF
    try:
        with _lock:
            t0 = time.time()
            if use_vision:
                prompt = model.tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
                out = model.generate(prompt, images=images, **gen_kw)
            else:
                prompt = model.tokenizer.apply_chat_template(messages, add_generation_prompt=True)
                out = model.generate(prompt, **gen_kw)
            if hasattr(model, "reset"):
                model.reset()
            wall = time.time() - t0
    except Exception as e:
        import traceback
        traceback.print_exc()
        return JSONResponse(status_code=500, content={"error": {"message": f"{type(e).__name__}: {e}"}})

    for p in images:
        try:
            os.remove(p)
        except OSError:
            pass

    prof = _profile_dict(out.profile)
    prof["wall_s"] = round(wall, 2)
    prof["model"] = model_id
    stats["requests"] += 1
    stats["last"] = prof

    text = out.text or ""
    return JSONResponse({
        "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model_id,
        "choices": [{"index": 0, "finish_reason": "stop",
                     "message": {"role": "assistant", "content": text}}],
        "usage": {"prompt_tokens": prof.get("prompt_tokens") or 0,
                  "completion_tokens": prof.get("generated_tokens") or 0,
                  "total_tokens": (prof.get("prompt_tokens") or 0) + (prof.get("generated_tokens") or 0)},
        "geniex_profile": prof,     # extra field: the NPU evidence
    })


if __name__ == "__main__":
    print(f"[npu] serving on http://{args.host}:{args.port}/v1  (device={args.device})", flush=True)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
