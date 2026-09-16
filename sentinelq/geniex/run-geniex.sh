#!/usr/bin/env bash
# Start Qualcomm GenieX as an OpenAI-compatible server on the UNO Q (Linux ARM64).
#
# The QRB2210 (Cortex-A53, ARMv8.0, no Hexagon NPU exposed) uses the CPU-only image,
# which needs neither --privileged nor the Qualcomm driver mount.
# Docs: https://geniex.aihub.qualcomm.com/en/run/linux/install
#
# Usage:  ./run-geniex.sh            # pulls model on first run, serves on :18181
#         GENIEX_MODEL=Qwen/Qwen3-0.6B-GGUF:Q4_0 ./run-geniex.sh
set -euo pipefail

IMAGE="${GENIEX_IMAGE:-docker.io/qualcomm/geniex:latest-cpu}"
MODEL="${GENIEX_MODEL:-unsloth/Qwen3.5-0.8B-GGUF:Q4_0}"
PORT="${GENIEX_PORT:-18181}"
DATA_DIR="${GENIEX_DATA:-$HOME/.geniex-data}"
mkdir -p "$DATA_DIR"

echo "[geniex] image=$IMAGE model=$MODEL port=$PORT"
docker pull "$IMAGE"

# 1) pull the GGUF once (cached in $DATA_DIR)
docker run --rm --network host \
  -v "$DATA_DIR:/data" \
  "$IMAGE" geniex pull "$MODEL"

# 2) serve. --network host keeps it reachable at 127.0.0.1:18181 from the host and
#    at the board IP from the App Lab app container.
exec docker run --rm --network host --name geniex \
  -v "$DATA_DIR:/data" \
  "$IMAGE" geniex serve --host 0.0.0.0 --port "$PORT"
