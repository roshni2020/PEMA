# Submission checklist (lablab.ai, Qualcomm track)

Deadline: online build window ends **September 16, 2026**. Confirm the exact hour and timezone
on the lablab event page before you rely on this.

- [ ] Public GitHub repo with this folder at the root and the README rendering the diagram
- [ ] 3-minute demo video (follow `docs/DEMO_SCRIPT.md`), screen + phone + board in frame
- [ ] Cover image: board, camera, dashboard on the phone
- [ ] lablab project page fields:
  - **Title:** PEMA: Personal Environmental Memory Agent (Arduino UNO Q + Snapdragon NPU)
  - **Short description:** Not surveillance: one enrolled person, a private on-device memory of
    their environment, and verified physical actions. It notices your keys moved while you were
    gone, tells you, turns the light on, and reads the microcontroller back to prove it. YOLOX
    and memory on the Arduino's Qualcomm Dragonwing, a grammar-constrained planner and vision
    model on the Hexagon NPU via GenieX, sensing and actuation on the STM32.
  - **Technologies:** Qualcomm GenieX (Python SDK, device_map="npu"), Qualcomm AI Hub,
    Snapdragon X Elite Hexagon NPU, Dragonwing QRB2210, Arduino App Lab, llama.cpp, Qwen3,
    Qwen3-VL, FastAPI/socket.io
  - **Qualcomm usage:** GenieX on the Hexagon NPU as the reasoning runtime with per-request
    profile (device, tok/s, ttft) shown in the UI; AI Hub YOLOX for perception on the
    Dragonwing; AI Hub Workbench export script for extra models
- [ ] Include the NPU evidence in the video: Task Manager NPU graph during a request, the
  dashboard log line with `npu` and tokens/s, and the `npu_bench.py` NPU vs CPU table
- [ ] Mention the honest limitation: the UNO Q's QRB2210 has no supported NPU, so NPU
  inference runs on the Snapdragon X Elite; on-board CPU mode is included
- [ ] Team members added on lablab, track set to Qualcomm

## Pitch in one paragraph

Two billion people live with some vision impairment and 55 million with dementia, and they ask
the same questions all day: where is my phone, what is in front of me, is the stove still on.
Cloud assistants answer with a subscription and a video feed of your home. SentinelQ answers
on the desk: YOLOX and an object memory on the Arduino UNO Q's Qualcomm Dragonwing chip, a
grammar-constrained planner and vision model on the Snapdragon Hexagon NPU via GenieX, and
deterministic safety rules with a hardware whitelist on the STM32. It is agentic in the real
sense: the planner chooses tools step by step, and every step, every action, and every NPU
profile is visible. Small models, strict schemas, and deterministic safety underneath, which
is the shape production edge agents will take.
