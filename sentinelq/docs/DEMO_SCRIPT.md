# 3-minute demo script: one loop, not a feature list

Setup: `python npu_server.py --vlm` on the laptop, `deploy-board.ps1` (starts the app and the USB
link), dashboard at http://127.0.0.1:7000 with the camera allowed, Task Manager → NPU visible.
Props: keys, a cup, a phone on the desk. Enroll yourself once beforehand (name + passphrase).

0:00  **Hook.** "Assistants that watch your home send video to a cloud and don't know what's yours.
      PEMA is the opposite: one enrolled person, a private memory of their environment on the
      device, and physical actions it verifies before it claims them."

0:20  **1. Identify, locally.** Say your passphrase. Pill flips to "Roshni present · verified",
      matrix shows the check icon. "That's a passphrase stored on the board. No face data."

0:40  **2. Remember.** Pan the camera over the desk. The memory panel fills: keys · on your left,
      at table height · now. "YOLOX on the Arduino's Qualcomm Dragonwing chip. It also learns each
      object's usual spot."

1:00  **3. Notice change.** Step out of frame (or press Camera off). After 30 s: "you left ·
      watching your things", matrix shows the eye. Move the keys to the other side. Step back in.
      The board fires the return event on its own: the trace runs safety → planner → speech and it
      says "Welcome back, Roshni. Your keys moved from your left to your right." Cut to Task Manager:
      the NPU graph spikes during the planner step.

1:50  **4. Act.** Say "Turn the light on." Planner → hardware. The relay clicks.

2:05  **5. Verify.** Point at the Verified actions panel: "light → on ✓ verified · MCU reports
      relay=1". "The microcontroller read the state back. If it hadn't, the trace would say so."
      Press "Light off": same, verified.

2:25  **The split.** Point at the header chips: Qualcomm silicon for perception, memory, reasoning;
      Arduino STM32 for real-time sensing and actuation. Show the Inference panel: Hexagon NPU,
      tokens/s, first token, Q4_0, grammar-constrained.

2:45  **Close.** "It notices your keys moved while you were gone, tells you, turns the light on and
      proves it did. Private, on-device, on Qualcomm. That's a product loop, not a demo."
