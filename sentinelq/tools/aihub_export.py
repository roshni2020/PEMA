"""Optional: pull a second Qualcomm AI Hub model and export it to TFLite for the QRB2210 CPU.

The YOLOX detector the App Lab brick uses already comes from AI Hub. This script shows
the AI Hub Workbench path for judges: compile a model from qai_hub_models, profile it on a
cloud device, download the .tflite, and drop it next to the app.

Run on your laptop (not the board):
    pip install qai_hub qai_hub_models
    qai-hub configure --api_token <token from aihub.qualcomm.com workbench settings>
    python tools/aihub_export.py --model mediapipe_hand --device "QCS6490 (Proxy)"

Note: QRB2210 is not in the AI Hub device list, so we compile for a nearby Dragonwing IoT
target and run the resulting TFLite on the UNO Q CPU with the tflite-runtime / LiteRT.
"""
import argparse
import importlib
import qai_hub as hub


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="yolov8_det", help="qai_hub_models model id, e.g. yolov8_det, mediapipe_hand")
    ap.add_argument("--device", default="QCS6490 (Proxy)")
    ap.add_argument("--out", default="models/")
    args = ap.parse_args()

    mod = importlib.import_module(f"qai_hub_models.models.{args.model}")
    model = mod.Model.from_pretrained()
    input_spec = model.get_input_spec()

    compile_job = hub.submit_compile_job(
        model=model.convert_to_torchscript(input_spec),
        device=hub.Device(args.device),
        input_specs=input_spec,
        options="--target_runtime tflite",
    )
    target = compile_job.get_target_model()
    profile_job = hub.submit_profile_job(model=target, device=hub.Device(args.device))
    prof = profile_job.download_profile()
    print("inference time (us):", prof["execution_summary"]["estimated_inference_time"])

    import os
    os.makedirs(args.out, exist_ok=True)
    path = os.path.join(args.out, f"{args.model}.tflite")
    target.download(path)
    print("saved", path)


if __name__ == "__main__":
    main()
