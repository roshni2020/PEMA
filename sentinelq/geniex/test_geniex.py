"""Smoke test for the GenieX server: run on the board (or over the LAN) before the demo.

    python3 test_geniex.py                 # uses http://127.0.0.1:18181/v1
    GENIEX_URL=http://<board-ip>:18181/v1 python3 test_geniex.py
"""
import os, sys, time, json
from openai import OpenAI

url = os.getenv("GENIEX_URL", "http://127.0.0.1:18181/v1")
model = os.getenv("GENIEX_MODEL", "unsloth/Qwen3.5-0.8B-GGUF:Q4_0")
client = OpenAI(base_url=url, api_key="geniex", timeout=120)

print("models:", [m.id for m in client.models.list().data])

snapshot = {"now_visible": {"person": 0.91, "knife": 0.62},
            "sensors": {"temp_c": 24.0, "door": 0, "light": 300, "relay": 1}}
system = open(os.path.join(os.path.dirname(__file__), "..", "python", "agent.py")).read()
system = system.split('SYSTEM_PROMPT = """')[1].split('"""')[0]

t0 = time.time()
r = client.chat.completions.create(
    model=model, temperature=0.2, max_tokens=160,
    messages=[{"role": "system", "content": system},
              {"role": "user", "content": f"SNAPSHOT:\n{json.dumps(snapshot)}\n\nCOMMAND: Patrol check."}])
dt = time.time() - t0
text = r.choices[0].message.content
print(text)
print(f"\n{r.usage.completion_tokens if r.usage else '?'} tokens in {dt:.1f}s")
sys.exit(0 if "{" in text else 1)
