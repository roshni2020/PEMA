# RoomMap - approximate 3D placement of fixtures and objects from a monocular sweep (backend).
#
# Scan: the frontend pans the camera (or plays an uploaded video) and sends sampled frames with
# their progress 0..1. A slow pan is treated as a sweep of `sweep_deg` degrees, so progress gives the
# direction; box position and size give the offset and a rough distance (world.room_coords).
#  - objects  : YOLOX on the board (the 80 COCO classes)
#  - fixtures : the vision model on the NPU is asked which large fixtures are visible (door,
#               window, table, bed, sofa, shelf, fridge, stove, sink, tv) every Nth frame.
# This is NOT a metric reconstruction. It is honest, cheap, and good enough to answer
# "where is the door" as "behind you, to the left, about three metres".

import os
import json
import math
import time
import threading

_APP_DIR = "/app" if os.path.isdir("/app") else os.getenv("APP_HOME", ".")
MAP_FILE = os.getenv("SQ_MAP_FILE", os.path.join(_APP_DIR, ".cache", "roommap.json"))

FIXTURES = ["door", "window", "table", "desk", "bed", "sofa", "chair", "shelf", "fridge", "stove", "sink", "tv", "wardrobe",
            "screen", "whiteboard", "stage", "counter", "cabinet"]
ALIASES = {"television": "tv", "monitor": "screen", "projector screen": "screen", "couch": "sofa", "doorway": "door",
           "refrigerator": "fridge", "cooker": "stove", "bookshelf": "shelf", "shelves": "shelf", "desks": "desk",
           "tables": "table", "chairs": "chair", "windows": "window", "doors": "door", "screens": "screen"}
# Open description instead of a checklist: a checklist makes small models tick every box.
FIXTURE_QUESTION = ("In one short sentence, name the large fixed things you can actually see in this photo "
                    "(for example a door, window, table, desk, chairs, sofa, bed, shelf, screen, whiteboard, counter) "
                    "and whether each is on the left, in the centre, or on the right. Do not guess; if unsure, say so.")
MAX_FIXTURES_PER_FRAME = 5      # more than this in one frame = the model is listing, not seeing


def parse_fixtures(answer: str):
    """'a door on the left, two chairs in the centre' -> [('door','left'), ('chair','center')]"""
    text = " " + answer.lower().replace(".", " ").replace(",", " , ") + " "
    for k, v in ALIASES.items():
        text = text.replace(" " + k + " ", " " + v + " ")
    found = []
    words = text.split()
    for i, w in enumerate(words):
        name = w.rstrip("s") if w.rstrip("s") in FIXTURES else (w if w in FIXTURES else None)
        if not name or name in [f for f, _ in found]:
            continue
        nxt = words[i + 1: i + 7]
        stop = next((k for k, w in enumerate(nxt) if w in (",", "and")), len(nxt))   # only this item's phrase
        window = " ".join(nxt[:stop])
        side = "left" if "left" in window else "right" if "right" in window else "center"
        found.append((name, side))
    return found


def bearing_words(ang, dist):
    a = ((ang + 180) % 360) - 180
    if abs(a) < 20:
        d = "straight ahead"
    elif abs(a) > 150:
        d = "behind you"
    elif a > 0:
        d = "to your right" if a < 70 else "to your right, behind you" if a > 110 else "on your right side"
    else:
        d = "to your left" if a > -70 else "to your left, behind you" if a < -110 else "on your left side"
    return f"{d}, about {max(1, round(dist))} metre{'s' if round(dist) != 1 else ''} away"


class RoomMap:
    """Several rooms, one active. Each room has its own scanned fixtures; object memory is shared."""

    def __init__(self, world, vision_fn=None):
        self.world = world
        self.vision_fn = vision_fn          # (jpeg_bytes, question) -> str, provided by the agent (NPU)
        self._lock = threading.Lock()
        self.rooms = {"Living room": {"fixtures": {}, "scanned_at": None, "sweep_deg": 360}}
        self.active = "Living room"
        self.scan = None                    # active scan state
        self._load()

    # convenience accessors for the active room
    @property
    def fixtures(self):
        return self.rooms[self.active]["fixtures"]

    @fixtures.setter
    def fixtures(self, v):
        self.rooms[self.active]["fixtures"] = v

    @property
    def scanned_at(self):
        return self.rooms[self.active]["scanned_at"]

    @scanned_at.setter
    def scanned_at(self, v):
        self.rooms[self.active]["scanned_at"] = v

    @property
    def sweep_deg(self):
        return self.rooms[self.active]["sweep_deg"]

    @sweep_deg.setter
    def sweep_deg(self, v):
        self.rooms[self.active]["sweep_deg"] = v

    def add_room(self, name):
        name = name.strip()[:40]
        with self._lock:
            if name and name not in self.rooms:
                self.rooms[name] = {"fixtures": {}, "scanned_at": None, "sweep_deg": 360}
            if name:
                self.active = name
            self._save()
        return self.view()

    def select_room(self, name):
        with self._lock:
            if name in self.rooms:
                self.active = name
                self._save()
        return self.view()

    # ---------------- persistence
    def _load(self):
        try:
            with open(MAP_FILE) as f:
                d = json.load(f)
            if "rooms" in d:
                self.rooms = d["rooms"] or self.rooms
                self.active = d.get("active") if d.get("active") in self.rooms else next(iter(self.rooms))
            elif "fixtures" in d:                      # old single-room file
                self.rooms[self.active] = {"fixtures": d.get("fixtures", {}), "scanned_at": d.get("scanned_at"),
                                           "sweep_deg": d.get("sweep_deg", 360)}
        except (OSError, ValueError):
            pass

    def _save(self):
        try:
            os.makedirs(os.path.dirname(MAP_FILE), exist_ok=True)
            with open(MAP_FILE, "w") as f:
                json.dump({"rooms": self.rooms, "active": self.active}, f)
        except OSError:
            pass

    # ---------------- scanning
    def start(self, sweep_deg=360, fixture_every=3):
        with self._lock:
            self.sweep_deg = float(sweep_deg)
            self.scan = {"frames": 0, "fixture_every": int(fixture_every), "started": time.time(),
                         "fixtures": {}, "objects": 0, "vision_calls": 0}
        return {"ok": True}

    def frame(self, progress: float, jpeg: bytes, detections: dict, frame_size):
        """Called for each sampled scan frame after YOLOX ran on the board."""
        with self._lock:
            if not self.scan:
                return None
            s = self.scan
            s["frames"] += 1
            angle = (progress * self.sweep_deg) % 360
            angle = (angle + 180) % 360 - 180
            do_vision = (s["frames"] - 1) % s["fixture_every"] == 0 and self.vision_fn is not None
        # objects: reuse the world memory with this frame's direction
        self.world.update_detections(detections, jpeg, frame_size=frame_size, view_angle=angle)
        with self._lock:
            self.scan["objects"] += len(detections)
        found = []
        if do_vision:
            try:
                answer = self.vision_fn(jpeg, FIXTURE_QUESTION) or ""
            except Exception as e:
                answer = ""
            with self._lock:
                self.scan["vision_calls"] += 1
                self.scan.setdefault("answers", []).append({"angle": angle, "answer": answer[:200]})
            pairs = parse_fixtures(answer)
            if len(pairs) > MAX_FIXTURES_PER_FRAME:
                pairs = []                                   # listing everything = not looking; discard
            for name, side in pairs:
                # the camera is mirrored for the user, so the model's "left" is the user's right
                off = 20 if side == "left" else -20 if side == "right" else 0
                a = (angle + off + 180) % 360 - 180
                with self._lock:
                    f = self.scan["fixtures"].setdefault(name, {"angs": [], "hits": 0})
                    f["angs"].append(a)
                    f["hits"] += 1
                found.append(name)
        with self._lock:
            return {"frame": self.scan["frames"], "angle": angle, "objects": list(detections.keys()),
                    "fixtures": found, "vision": do_vision}

    def finish(self):
        with self._lock:
            if not self.scan:
                return self.view()
            fx = {}
            calls = max(1, self.scan.get("vision_calls", 1))
            min_hits = 1 if calls < 3 else 2               # need two independent sightings when we have enough frames
            for name, f in self.scan["fixtures"].items():
                if f["hits"] < min_hits:
                    continue
                # circular mean of the angles this fixture was seen at
                x = sum(math.cos(math.radians(a)) for a in f["angs"])
                y = sum(math.sin(math.radians(a)) for a in f["angs"])
                ang = round(math.degrees(math.atan2(y, x)), 1)
                fx[name] = {"ang": ang, "dist": 3.0, "hits": f["hits"]}
            self.fixtures = fx
            self.scanned_at = time.time()
            stats = {k: v for k, v in self.scan.items() if k != "fixtures"}
            self.scan = None
            self._save()
        out = self.view()
        out["stats"] = stats
        return out

    # ---------------- queries
    def where_is_fixture(self, name: str):
        name = name.lower().strip()
        with self._lock:
            f = self.fixtures.get(name) or next((v for k, v in self.fixtures.items() if name in k or k in name), None)
        if not f:
            return None
        rel = f["ang"] - self.world.view_angle
        return f"{name}: {bearing_words(rel, f['dist'])} (from the room scan)."

    def view(self):
        with self._lock:
            return {"room": self.active,
                    "rooms": [{"name": n, "fixtures": len(r["fixtures"]), "scanned_at": r["scanned_at"]} for n, r in self.rooms.items()],
                    "fixtures": self.fixtures, "scanned_at": self.scanned_at, "sweep_deg": self.sweep_deg,
                    "scanning": bool(self.scan), "view_angle": self.world.view_angle,
                    "objects": self.world.memory_view(30)}
