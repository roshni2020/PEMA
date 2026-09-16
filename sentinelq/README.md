# PEMA: Personal Environmental Memory Agent

*(codename SentinelQ, the app id on the board)*

**Not surveillance. One enrolled person, a private on-device memory of their environment, and
physical actions that are verified before they are claimed.**
Qualcomm track, AI Infra Summit Hackathon 2026.

The loop, end to end:

1. **Identify the enrolled user, locally.** A name and a private passphrase stored on the board.
   No face data, no cloud, no strangers identified.
2. **Remember the environment over time.** YOLOX on the Arduino's Qualcomm Dragonwing chip sees
   objects; the board keeps where each was last seen, its usual spot, and a trail, across restarts.
3. **Notice a meaningful change.** When the person leaves, the memory is snapshotted. When they
   return, PEMA says unprompted: "Your keys moved from the table on your left to your right."
4. **Take a physical action.** A grammar-constrained planner on the Snapdragon Hexagon NPU
   (GenieX) chooses tools step by step: memory, vision, hardware, speech.
5. **Verify it worked.** Every action is read back from the STM32 microcontroller. The trace shows
   "light on · verified by MCU", never an unconfirmed claim.

**Pages (frontend, `assets/`):** `login.html` register or sign in (name + passphrase, stored on the
board), `app.html` the dashboard (Room map in approximate 3D, Camera, Timeline, System tabs, plus
assistant, agent trace, memory, verified actions), `rooms.html` manage rooms and scan each one by
panning the camera or dropping a short video. **Backend (`python/`)** runs on the board.

**Privacy, plainly (this is built for low-vision and memory-impaired users, not for watching people):**
camera frames are analysed on the device and discarded; only object names and positions are kept,
in SQLite and JSON on the board; it recognises objects, not faces; the user is verified by
passphrase, never biometrics; the camera has an off switch and the board's LED shows when it is
watching; the reasoning model runs on a local Qualcomm NPU, no internet after setup.

The split is explicit: **Qualcomm silicon** (Dragonwing on the board, Hexagon NPU on the laptop)
does perception, memory and reasoning; the **Arduino STM32** does real-time sensing and verified
actuation. Nothing leaves the local network.
See `docs/ARCHITECTURE.md` for the agents and `docs/DEMO_SCRIPT.md` for the demo.

```
 Snapdragon X Elite laptop (Windows ARM64)             Arduino UNO Q
 ┌──────────────────────────────────┐   Wi-Fi    ┌───────────────────────────────────────┐
 │ GenieX Python SDK                │◀──────────▶│ Dragonwing QRB2210 (Debian Linux)      │
 │  Qwen3 LLM  ── device_map="npu"  │ OpenAI API │  YOLOX object detection (AI Hub model) │
 │  Qwen3-VL   ── Hexagon NPU       │ + profile  │  "Hey Arduino" keyword spotting        │
 │ geniex/npu_server.py :18182      │            │  WebUI dashboard, browser voice        │
 │ (or `geniex serve` :18181)       │            │  agent: rules.py → GenieX → whitelist  │
 └──────────────────────────────────┘            │ ────────── Bridge RPC ──────────       │
                                                 │ STM32U585: matrix · buzzer · relay ·   │
   Fully-on-board mode (no laptop):              │ servo · temp/door/light · local        │
   GenieX CPU container on the UNO Q             │ over-temp interlock                    │
                                                 └───────────────────────────────────────┘
```

## Why this matters for the Qualcomm track

- **GenieX on the Hexagon NPU.** `geniex/npu_server.py` loads the model with
  `device_map="llama_cpp:HTP0"` (GGUF on the Hexagon tensor processor) or `qairt`
  (AI Hub bundles) through the GenieX Python SDK and returns the GenieX profile with every
  answer: device, backend, prefill and decode tokens/s, time to first token. The UNO Q
  dashboard prints those numbers next to each decision. `geniex/npu_bench.py` runs the same
  prompt on NPU and CPU back to back for the video.
- **AI Hub models in the perception loop.** The App Lab object-detection brick runs YOLOX
  from Qualcomm AI Hub on the QRB2210. `tools/aihub_export.py` shows the AI Hub Workbench path
  for adding another model (compile, profile, download TFLite).
- **One API, three placements.** The agent talks OpenAI-compatible HTTP. Point `GENIEX_URL`
  at the laptop NPU, at `geniex serve`, or at the CPU-only GenieX container on the board
  itself (fully offline demo mode). Nothing else changes.
- **Grammar-constrained decoding on the NPU.** The agent sends a llama.cpp GBNF grammar
  (`PLAN_GBNF` in `python/agent.py`) with every request; `npu_server.py` passes it to GenieX
  `generate(grammar=...)`. The 2B model can then only emit the exact plan schema with
  whitelisted action types, which fixed the malformed JSON we saw from Q4_0 without it.
- **Split processing with real safety.** Probabilistic reasoning on the accelerator,
  deterministic rules and a whitelist in Python, hard interlocks on the MCU. The LLM can only
  emit actions the sketch exposes (`python/actions.py`).

## Hardware

- Arduino UNO Q (2 GB or 4 GB), USB-C hub with power delivery, USB webcam.
- A Snapdragon X Elite / X2 Elite laptop for the NPU (any Copilot+ PC). Without one, use
  fully-on-board mode (CPU) and the same code runs.
- Optional: USB mic for the wake word, passive buzzer on D8, relay on D7, servo on D9,
  reed switch on D2, TMP36 on A0, LDR on A1. The built-in LED matrix alone is enough.

## Setup

### A. Laptop: GenieX on the NPU (10 minutes)

```powershell
cd sentinelq\geniex
.\setup-laptop.ps1            # ARM64 Python, VC++ runtime, geniex, fastapi, firewall rule
python npu_server.py --vlm    # first run downloads Qwen3.5-2B GGUF Q4_0 (~1.2 GB + mmproj), text + vision
```
Verify: `curl http://127.0.0.1:18182/v1/models` and open Task Manager → Performance → NPU.
Note the laptop's Wi-Fi IP printed by the setup script (for example 10.4.0.102).
Prefer the official CLI? Run `geniex-cli.exe` from Downloads, then `geniex serve` (port 18181).
The agent works with both, the profile numbers only come from `npu_server.py`.

### B. Board: deploy over USB (5 minutes, no desktop App Lab needed)

The UNO Q ships with `arduino-app-cli`, Docker and every brick preinstalled, and exposes an
ADB interface over USB-C. The Arduino IDE's "Arduino Q Boards" core installs `adb.exe`.

1. Install the Arduino IDE, select "Arduino UNO Q" once so the Q core (with adb) installs.
2. Put the laptop's Wi-Fi IP in `python/agent.py` (`_CANDIDATE_URLS`, first entry) or set
   `GENIEX_URL`. Then:
   ```powershell
   cd sentinelq\geniex
   .\deploy-board.ps1 -Logs      # pushes the app, compiles + flashes the STM32, starts the container
   .\usb-link.ps1                # optional: USB tunnel if the venue Wi-Fi isolates clients
   ```
3. Dashboard: `http://127.0.0.1:7000` on the laptop (USB) or `http://<board-ip>:7000` on a
   phone (Wi-Fi). REST test without the UI:
   `curl "http://127.0.0.1:7000/command?text=Show%20the%20heart%20icon"`
4. Plug a USB webcam into the board and uncomment `arduino:video_object_detection` in
   `app.yaml` (the app refuses to start that brick with no camera). Same for the mic and
   `arduino:keyword_spotting` plus `SQ_ENABLE_KWS=1`.

Desktop App Lab works too (open the folder, press Run), it is just not required.

### C. Fully-on-board mode (optional, no laptop)

```bash
ssh arduino@<board-ip>
cd ~/sentinelq/geniex && ./run-geniex.sh       # GenieX CPU container, Qwen3.5-0.8B Q4_0
```
Leave `GENIEX_URL` unset and the agent finds it on 127.0.0.1:18181.

## Using it

- Type or speak: "Is anyone in the room?", "If it is dark, turn the lamp relay on",
  "Point the servo at the door", "What do you see?" (uses the VLM with the live frame).
- Toggle **Autonomous patrol**: every 5 s the agent reviews the scene unprompted and acts.
- Say "Hey Arduino" (with a mic) and the agent describes what it sees.
- Manual override buttons bypass the LLM so you can always show the hardware works.

## Configuration

| Env var | Default | Meaning |
|---|---|---|
| `GENIEX_URL` | probes :18182, :18181, container hosts | GenieX server, set to the laptop for NPU mode |
| `GENIEX_MODEL` | first model the server lists | override model id |
| `GENIEX_MAX_TOKENS` | 200 | answer budget |
| `SQ_CONFIDENCE` | 0.45 | YOLOX threshold |
| `SQ_TICK_SECONDS` | 5 | patrol cadence |
| `SQ_WAKE_LABEL` | `hey_arduino` | label emitted by the KWS model; check the brick's labels |
| `SQ_LLM`, `SQ_VLM`, `SQ_DEVICE` | `unsloth/Qwen3.5-2B-GGUF`, same, `llama_cpp:HTP0` | npu_server.py model and device (HTP0 = Hexagon NPU; `llama_cpp:CPU` for baseline; `ai-hub-models/*` ids use QAIRT automatically) |

## Project layout

```
sentinelq/
  app.yaml                 App Lab manifest: bricks used
  python/
    main.py                wiring: bricks, Bridge, WebUI, decision loop
    agent.py               GenieX client (OpenAI API), prompt, JSON parsing, VLM frames, fallback
    rules.py               deterministic safety rules run before the LLM
    actions.py             whitelist + dispatch to the sketch over Bridge
    world.py               world state with 30 s history and latest camera frame
    requirements.txt
  sketch/sketch.ino        STM32: actuators, sensors, local interlock
  assets/                  dashboard (index.html, app.js, style.css)
  geniex/
    npu_server.py          OpenAI-compatible server on the GenieX Python SDK, NPU, /stats
    npu_bench.py           NPU vs CPU benchmark clip
    setup-laptop.ps1       Windows ARM64 setup
    run-geniex.sh, geniex.service, test_geniex.py   on-board CPU mode
  tools/aihub_export.py    optional AI Hub Workbench compile/profile/export
  docs/                    ARCHITECTURE.md, DEMO_SCRIPT.md, SUBMISSION.md
```

## Known limits

- The QRB2210 on the UNO Q has no Hexagon NPU exposed to GenieX, so NPU inference runs on
  the Snapdragon X Elite laptop. On-board mode uses the 4 Cortex-A53 cores (about 4 to 8
  tokens/s with a 0.8B Q4_0 model). Rules fire instantly regardless.
- Small models sometimes wrap JSON in prose or `<think>` tags; `agent.py` strips both and
  rejects anything that does not validate.
- ASR and TTS bricks are VENTUNO Q only. Voice-in uses the browser's speech API and voice-out
  uses browser speech synthesis. The wake word brick does run on UNO Q.
