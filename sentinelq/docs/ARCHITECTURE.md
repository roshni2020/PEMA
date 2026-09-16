# Architecture

## The agents

Every request runs through a small team of agents. Each step is streamed to the dashboard's
"Agent trace" panel so judges can see who did what.

| Agent | Runs on | What it does | Code |
|---|---|---|---|
| **Perception** | UNO Q, Dragonwing QRB2210 | YOLOX (int8, Edge Impulse runner) on every camera frame; keeps the object memory: where each object was last seen, as words, and when | `main.py` (`ws_frame`), `world.py` |
| **Safety** | UNO Q, Python, no model | Deterministic rules (sharp object left out, unattended stove, over-temperature, dark room) plus the action whitelist. Runs first on every request and can veto anything the planner asks for | `rules.py`, `actions.py` |
| **Planner** | Snapdragon Hexagon NPU via GenieX | Qwen3.5-2B Q4_0. Picks one tool per step, loops until it speaks. Output is grammar-constrained (GBNF) so it can only emit valid steps and whitelisted actions | `agent.py` (`run`, `STEP_GBNF`) |
| **Memory tool** | UNO Q | `where_is(object)` answers from the object memory without any model | `agent.py` (`_where_is`) |
| **Vision tool** | Hexagon NPU via GenieX | `look(question)` sends the live frame to the same multimodal model | `agent.py` (`_look`) |
| **Hardware** | STM32U585 over Bridge RPC | `act(actions)`: matrix icons, buzzer, relay, servo, LED. Local over-temperature interlock that never depends on Linux | `sketch/sketch.ino` |
| **Speech** | Dashboard (browser) | `say(text)` is spoken aloud; voice input via browser speech recognition; "Hey Arduino" wake word brick when a USB mic is attached | `assets/app.js`, `main.py` |

## One request, step by step

```
"Where is my phone?"
  safety    no rule triggered
  planner   thought: check memory          tool: where_is {"object":"cell phone"}
  memory    cell phone: on your right, at table height, close, seen 40 seconds ago
  planner   thought: tell the person       tool: say {"text": "Your phone is on your right, ..."}
  speech    (spoken through the dashboard)
```

```
"What is in front of me?"
  safety    no rule triggered
  planner   tool: look {"question": "What is in front of me?"}
  vision    (camera frame -> NPU) "A cup and a laptop on the table, keys to the left."
  planner   tool: say {...}
  speech
```

## Data flow

1. The dashboard captures a webcam frame every 1.5 s and sends it to the board over the websocket.
2. The perception agent runs YOLOX, updates the object memory (position words are computed from
   the bounding box: left/right/ahead, high/table/low, close/far), and returns the annotated frame.
3. The sketch pushes `temp_c;door;light;relay` every second via `Bridge.notify`.
4. A request arrives (typed, spoken, wake word, or the 5 s watch-mode tick).
5. Safety rules run and may act immediately.
6. The planner loop runs on the NPU: up to 4 steps, each a `POST /v1/chat/completions` with the
   step grammar. Tool results are fed back as messages.
7. `actions.py` validates every hardware action against the whitelist before `Bridge.call`.
8. Every step, action, and GenieX profile (device, tok/s, ttft, tokens) is emitted to the UI.

## Inference optimisation, what is real

- Q4_0 weights on the Hexagon NPU (llama.cpp HTP backend) via GenieX `device_map="llama_cpp:HTP0"`.
- Grammar-constrained decoding: no retries, no malformed JSON, fewer wasted tokens.
- One multimodal model for both text and vision (no second model in memory).
- Frames downscaled to 640 px, JPEG q=0.6, one in flight at a time (backpressure).
- Short system prompt and compact snapshot JSON; profile numbers shown per request.
- `geniex/npu_bench.py` prints NPU vs GPU vs CPU tokens/s for the same prompt.

## Placement is a config value

The agent only speaks OpenAI-compatible HTTP. `GENIEX_URL` (or the probe list in `agent.py`)
decides where the model lives:

| Mode | URL | Device |
|---|---|---|
| Laptop NPU over USB (default at the venue) | `http://msgpack-rpc-router:18184/v1` via `usb-link.ps1` | Hexagon NPU |
| Laptop NPU over Wi-Fi | `http://<laptop-ip>:18182/v1` | Hexagon NPU |
| Fully on-board | `http://127.0.0.1:18181/v1` (`run-geniex.sh`) | UNO Q CPU |
| Dragonwing board with NPU (VENTUNO Q, IQ-9075) | `http://127.0.0.1:18181/v1` | Hexagon via QAIRT |
