# PEMA - Personal Environmental Memory Agent (backend, runs on the Arduino UNO Q's Linux side)
#
# Layout
#   python/   backend: this file (wiring), world.py (memory + presence), roommap.py (scan + map),
#             agent.py (planner loop on the NPU), rules.py (safety), actions.py (verified hardware),
#             store.py (SQLite history)
#   assets/   frontend: index.html + js/*.js + css, served by the WebUI brick, talks over socket.io
#   sketch/   STM32 firmware: sensing, actuation, state readback
#
# Loop: enrolled user -> memory -> change detection -> action -> MCU-verified result.

import os
import io
import time
import json
import base64
import threading

from arduino.app_utils import App, Bridge, Logger
from arduino.app_bricks.web_ui import WebUI

from store import Store
from world import WorldState
from roommap import RoomMap
from agent import Agent
from actions import ActionExecutor

logger = Logger("PEMA")

CONFIDENCE = float(os.getenv("SQ_CONFIDENCE", "0.45"))
TICK_SECONDS = float(os.getenv("SQ_TICK_SECONDS", "5"))
WAKE_LABEL = os.getenv("SQ_WAKE_LABEL", "hey_arduino")
ENABLE_KWS = os.getenv("SQ_ENABLE_KWS", "0") == "1"
APP_DIR = "/app" if os.path.isdir("/app") else "."
PROFILE_FILE = os.path.join(APP_DIR, ".cache", "profile.json")

# ---------------------------------------------------------------- core objects
ui = WebUI()
store = Store()
world = WorldState(history_seconds=30, store=store)
executor = ActionExecutor(ui=ui, world=world)
agent = Agent(ui=ui, world=world, executor=executor)
roommap = RoomMap(world, vision_fn=agent.vision_text)
agent.roommap = roommap
executor.on_action = lambda src, act, ok, detail: store.action(src, act, ok, detail)

# ---------------------------------------------------------------- perception (YOLOX on the board)
image_detector = None
try:
    from arduino.app_bricks.object_detection import ObjectDetection
    image_detector = ObjectDetection()
    logger.info("Image object detection (YOLOX) ready for browser frames")
except Exception as e:
    logger.warning(f"Image object detection unavailable: {e}")


def detect(jpeg: bytes):
    """jpeg -> (pil, detections{label: [{confidence, bounding_box_xyxy}]}, raw_results, ms)"""
    from PIL import Image
    pil = Image.open(io.BytesIO(jpeg)).convert("RGB")
    t0 = time.time()
    results = image_detector.detect(pil, confidence=CONFIDENCE) or {} if image_detector else {}
    ms = round((time.time() - t0) * 1000)
    dets = {}
    for d in results.get("detection", []):
        label = d.get("class_name") or d.get("label") or "object"
        conf = float(str(d.get("confidence", 0)).replace("%", "") or 0)
        if conf > 1.0:
            conf /= 100.0
        dets.setdefault(label, []).append({"confidence": conf, "bounding_box_xyxy": d.get("bounding_box_xyxy")})
    return pil, dets, results, ms


# Instant reactions to specific objects appearing (no model involved): {label: (beep pattern, icon, said)}
WATCH_LIST = {
    "cell phone": ("short", "eye", "I see your phone."),
    "knife": ("double", "warning", "Careful, a knife is in view."),
    "scissors": ("double", "warning", "Careful, scissors are in view."),
}
_watch_last = {}


def watch_alerts(dets: dict):
    now = time.time()
    for label, (pattern, icon, say) in WATCH_LIST.items():
        if label in dets and now - _watch_last.get(label, 0) > 20:
            _watch_last[label] = now
            pos = world.memory.get(label, {}).get("position", "in view")
            threading.Thread(target=executor.run,
                             args=([{"type": "buzzer", "pattern": pattern}, {"type": "matrix", "icon": icon}],),
                             kwargs={"reason": f"{label} appeared", "source": "perception", "say": f"{say} {pos.capitalize()}."},
                             daemon=True).start()
            store.event("watch", f"{label} appeared {pos}")


def ws_frame(client_id, data):
    """Live camera frame from the page -> YOLOX -> memory/presence -> annotated frame back."""
    try:
        jpeg = base64.b64decode((data or {}).get("image", ""))
        if not jpeg:
            return
        pil, dets, results, ms = detect(jpeg)
        event = world.update_detections(dets, jpeg, frame_size=pil.size)
        watch_alerts(dets)
        ui.send_message("detections", world.detection_summary())
        if event:
            on_presence_event(event)
        annotated = image_detector.draw_bounding_boxes(pil, results) if (image_detector and results.get("detection")) else None
        out = io.BytesIO()
        (annotated or pil).convert("RGB").save(out, format="JPEG", quality=70)
        ui.send_message("frame_result", {"image": base64.b64encode(out.getvalue()).decode(),
                                         "count": len(results.get("detection", [])), "ms": ms}, room=client_id)
    except Exception as e:
        logger.warning(f"frame handling failed: {e}")
        ui.send_message("frame_result", {"error": str(e)}, room=client_id)


# ---------------------------------------------------------------- room scan (pan or uploaded video)
def ws_scan_start(client_id, data):
    d = data or {}
    roommap.start(sweep_deg=float(d.get("sweep_deg", 360)), fixture_every=int(d.get("fixture_every", 3)))
    store.event("scan_start", d)
    ui.send_message("scan", {"state": "started"})


import queue
_scan_q = queue.Queue()


def _scan_worker():
    """Scan frames are processed off the socket thread: a vision call can take 10-20 s and must
    not stall the websocket heartbeat."""
    while True:
        client_id, data = _scan_q.get()
        try:
            jpeg = base64.b64decode((data or {}).get("image", ""))
            progress = float((data or {}).get("progress", 0.0))
            pil, dets, results, ms = detect(jpeg)
            info = roommap.frame(progress, jpeg, dets, pil.size)
            if info:
                info.update({"ms": ms, "progress": progress})
                ui.send_message("scan", {"state": "frame", **info})
                ui.send_message("detections", world.detection_summary())
        except Exception as e:
            logger.warning(f"scan frame failed: {e}")
            ui.send_message("scan", {"state": "error", "error": str(e)})
        finally:
            _scan_q.task_done()


threading.Thread(target=_scan_worker, daemon=True).start()


def ws_scan_frame(client_id, data):
    _scan_q.put((client_id, data))


def ws_scan_finish(client_id, data):
    _scan_q.join()                      # wait for queued frames (vision calls) before finishing
    m = roommap.finish()
    stats = m.get("stats") or {}
    for a in stats.pop("answers", []):                      # raw vision answers, for debugging the scan
        store.event("fixture_answer", f"{round(a['angle'])}deg: {a['answer']}")
    store.event("scan_done", {"fixtures": list(m.get("fixtures", {}).keys()), "stats": stats})
    ui.send_message("scan", {"state": "done", **m})
    ui.send_message("roommap", roommap.view())
    executor.run([{"type": "matrix", "icon": "check"}, {"type": "buzzer", "pattern": "short"}],
                 reason="scan complete", source="scan",
                 say=f"Room scanned. I found {len(m.get('fixtures', {}))} fixtures and {len(m.get('objects', []))} objects.")


def ws_set_view(client_id, data):
    world.set_view_angle(float((data or {}).get("angle", 0)))
    ui.send_message("roommap", roommap.view())


def ws_room_add(client_id, data):
    ui.send_message("roommap", roommap.add_room(str((data or {}).get("name", ""))))


def ws_room_select(client_id, data):
    ui.send_message("roommap", roommap.select_room(str((data or {}).get("name", ""))))


def ws_login(client_id, data):
    """Register (first user) or sign in (passphrase must match). Local file on the board, no cloud."""
    name = str((data or {}).get("name", "")).strip()[:40]
    phrase = str((data or {}).get("phrase", "")).strip().lower()[:80]
    if not name or not phrase:
        return ui.send_message("login_result", {"ok": False, "error": "name and passphrase required"}, room=client_id)
    if _profile is None:
        ws_enroll(client_id, {"name": name, "phrase": phrase})
        store.event("registered", name)
        return ui.send_message("login_result", {"ok": True, "name": name, "registered": True}, room=client_id)
    if phrase == _profile["phrase"]:
        world.verify_owner()
        store.event("login", _profile["name"])
        ui.send_message("presence", {"event": "verified", **world.presence_view()})
        executor.run([{"type": "matrix", "icon": "check"}], reason="signed in", source="enroll",
                     say=f"Welcome back, {_profile['name']}.")
        return ui.send_message("login_result", {"ok": True, "name": _profile["name"]}, room=client_id)
    store.event("login_failed", name)
    ui.send_message("login_result", {"ok": False, "error": f"wrong passphrase for {_profile['name']}"}, room=client_id)


def ws_forget(client_id, data):
    world.clear_memory()
    store.event("memory_cleared", "")
    ui.send_message("detections", world.detection_summary())
    ui.send_message("roommap", roommap.view())
    ui.send_message("say", {"text": "Memory cleared. I will start remembering from now.", "source": "system"})


ui.on_message("forget", ws_forget)
ui.on_message("frame", ws_frame)
ui.on_message("room_add", ws_room_add)
ui.on_message("room_select", ws_room_select)
ui.on_message("login", ws_login)
ui.on_message("scan_start", ws_scan_start)
ui.on_message("scan_frame", ws_scan_frame)
ui.on_message("scan_finish", ws_scan_finish)
ui.on_message("set_view", ws_set_view)
ui.expose_api("GET", "/map", lambda: roommap.view())
ui.expose_api("GET", "/timeline", lambda seconds: store.timeline(int(seconds or 1800)))
ui.expose_api("GET", "/history", lambda: store.counts())

# ---------------------------------------------------------------- session: enrollment + presence
_profile = None


def load_profile():
    global _profile
    try:
        with open(PROFILE_FILE) as f:
            _profile = json.load(f)
        world.owner = {"name": _profile["name"]}
    except (OSError, ValueError, KeyError):
        _profile = None


load_profile()


def on_presence_event(event: str):
    pv = world.presence_view()
    ui.send_message("presence", {"event": event, **pv})
    store.event(event, pv["last_changes"])
    logger.info(f"presence: {event} changes={pv['last_changes']}")
    if event == "left":
        executor.run([{"type": "matrix", "icon": "eye"}], reason="owner left, watching", source="perception")
    elif event == "returned":
        n = len(pv["last_changes"])
        text = ("The person just came back. " +
                (f"{n} thing(s) changed while they were away (see changes_since_owner_left). Tell them what moved, briefly."
                 if n else "Nothing moved while they were away. Welcome them in a few words."))
        threading.Thread(target=handle_command, args=(text, "return"), daemon=True).start()
    elif event == "arrived":
        executor.run([{"type": "matrix", "icon": "check"}], reason="person present", source="perception")


def ws_enroll(client_id, data):
    """Local enrollment: name + passphrase stored on the board. No face data, no cloud."""
    global _profile
    name = str((data or {}).get("name", "")).strip()[:40]
    phrase = str((data or {}).get("phrase", "")).strip().lower()[:80]
    if not name or not phrase:
        return {"ok": False, "error": "name and passphrase required"}
    _profile = {"name": name, "phrase": phrase}
    os.makedirs(os.path.dirname(PROFILE_FILE), exist_ok=True)
    with open(PROFILE_FILE, "w") as f:
        json.dump(_profile, f)
    world.owner = {"name": name}
    world.verify_owner()
    store.event("enrolled", name)
    ui.send_message("presence", {"event": "enrolled", **world.presence_view()})
    executor.run([{"type": "matrix", "icon": "heart"}, {"type": "buzzer", "pattern": "short"}],
                 reason="enrolled", source="enroll", say=f"Nice to meet you, {name}. I will remember your things.")
    return {"ok": True}


def check_passphrase(text: str) -> bool:
    if _profile and _profile["phrase"] in text.lower():
        world.verify_owner()
        store.event("verified", _profile["name"])
        ui.send_message("presence", {"event": "verified", **world.presence_view()})
        return True
    return False


ui.on_message("enroll", ws_enroll)
ui.expose_api("GET", "/presence", lambda: world.presence_view())


# ---------------------------------------------------------------- MCU -> Python
def on_sensors(payload: str):
    world.update_sensors(payload)
    ui.send_message("sensors", world.sensors)


Bridge.provide("on_sensors", on_sensors)

# ---------------------------------------------------------------- the decision loop
_busy = threading.Lock()


def handle_command(text: str, source: str):
    if not _busy.acquire(blocking=False):
        ui.send_message("status", {"state": "busy", "note": "agent already thinking"})
        return
    try:
        ui.send_message("status", {"state": "thinking", "source": source, "command": text})
        if source in ("dashboard", "wakeword") and check_passphrase(text):
            name = (world.owner or {}).get("name", "")
            executor.run([{"type": "matrix", "icon": "check"}], reason="owner verified", source="enroll",
                         say=f"Welcome back, {name}.")
            ui.send_message("status", {"state": "idle"})
            return
        agent.run(text, source=source)
        ui.send_message("status", {"state": "idle"})
    except Exception as e:
        logger.error(f"agent run failed: {e}")
        ui.send_message("status", {"state": "idle", "note": f"error: {e}"})
    finally:
        _busy.release()


def patrol_loop():
    time.sleep(TICK_SECONDS)
    event = world.tick()
    if event:
        on_presence_event(event)
    if world.autonomous:
        handle_command("Routine check. Only speak if there is a hazard or something the person should know.",
                       source="patrol")


# ---------------------------------------------------------------- dashboard -> Python
def ws_command(client_id, data):
    text = (data or {}).get("text", "").strip()
    if text:
        threading.Thread(target=handle_command, args=(text, "dashboard"), daemon=True).start()


def ws_set_mode(client_id, data):
    world.autonomous = bool((data or {}).get("autonomous", False))
    ui.send_message("status", {"state": "idle", "autonomous": world.autonomous})


def ws_manual_action(client_id, data):
    executor.run([data], reason="manual override", source="dashboard")


def ws_hello(client_id):
    ui.send_message("status", {"state": "idle", "autonomous": world.autonomous, "llm": agent.describe()})
    ui.send_message("presence", {"event": "hello", **world.presence_view()})
    ui.send_message("sensors", world.sensors)
    ui.send_message("detections", world.detection_summary())
    ui.send_message("roommap", roommap.view())


ui.on_connect(ws_hello)
ui.on_message("command", ws_command)
ui.on_message("set_mode", ws_set_mode)
ui.on_message("manual_action", ws_manual_action)
ui.expose_api("GET", "/state", lambda: world.snapshot())
ui.expose_api("GET", "/memory", lambda: {"objects": world.memory_view(50)})
ui.expose_api("GET", "/health", lambda: {"ok": True, "llm": agent.describe(), "history": store.counts()})


def http_command(text: str = "Routine check."):
    handle_command(text, source="api")
    return {"ok": True, "llm": agent.describe(), "last_actions": list(world.last_actions)}


ui.expose_api("GET", "/command", http_command)

# ---------------------------------------------------------------- optional wake word (USB mic)
if ENABLE_KWS:
    try:
        from arduino.app_bricks.keyword_spotting import KeywordSpotting
        kws = KeywordSpotting(confidence=0.85, debounce_sec=3.0)

        def on_wake():
            ui.send_message("wake", {})
            threading.Thread(target=handle_command,
                             args=("The person called you. Briefly say what is in front of them and any hazard.", "wakeword"),
                             daemon=True).start()

        kws.on_detect(WAKE_LABEL, on_wake)
    except Exception as e:
        logger.warning(f"Keyword spotting disabled: {e}")


logger.info("PEMA starting")
App.run(user_loop=patrol_loop)
