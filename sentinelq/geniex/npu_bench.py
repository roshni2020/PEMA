"""Side-by-side NPU vs CPU benchmark with the GenieX Python SDK. Great 20-second demo clip.

    python npu_bench.py                      # Qwen3-1.7B Q4_0, npu then cpu
    python npu_bench.py --model Qwen/Qwen3-0.6B-GGUF
Open Task Manager > Performance > NPU while it runs.
"""
import argparse, time
from geniex import AutoModelForCausalLM

ap = argparse.ArgumentParser()
ap.add_argument("--model", default="Qwen/Qwen3-1.7B-GGUF")
ap.add_argument("--devices", default="llama_cpp:HTP0,llama_cpp:GPUOpenCL,llama_cpp:CPU",
                help="GenieX device maps; HTP0 = Hexagon NPU")
ap.add_argument("--tokens", type=int, default=128)
a = ap.parse_args()

PROMPT = [{"role": "user", "content": "You are a workshop guardian. A person and scissors are in view. "
                                      "Reply with a JSON plan with reasoning, say, actions."}]
rows = []
for dev in a.devices.split(","):
    t0 = time.time()
    m = AutoModelForCausalLM.from_pretrained(a.model, precision="Q4_0", device_map=dev)
    load = time.time() - t0
    prompt = m.tokenizer.apply_chat_template(PROMPT, add_generation_prompt=True)
    out = m.generate(prompt, max_new_tokens=a.tokens)
    p = out.profile
    rows.append((dev, p.device, p.backend, load, p.ttft, p.prefill_speed, p.decode_speed, p.generated_tokens))
    m.close()

print(f"\n{'req':6} {'device':10} {'backend':10} {'load s':>7} {'ttft':>7} {'prefill t/s':>12} {'decode t/s':>11} {'tokens':>6}")
for r in rows:
    print(f"{r[0]:6} {str(r[1]):10} {str(r[2]):10} {r[3]:7.1f} {r[4]:7.2f} {r[5]:12.1f} {r[6]:11.1f} {r[7]:6}")
