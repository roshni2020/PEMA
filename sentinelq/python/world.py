# WorldState - the single source of truth the agent reasons over (backend, runs on the board).
#
# OBJECT MEMORY: for every object class the camera has seen we keep where it was last seen, both as
# words a person can act on ("on your right, at table height, close") and as an approximate room
# position (angle from the camera's front, distance in metres estimated from the box size), when,
# a short trail, and the frequency of each spot ("usual spot"). Persisted across restarts.
#
# PRESENCE + CHANGE DETECTION: when the person leaves, the memory is snapshotted; when they return,
# the diff (moved / appeared / not seen) is computed and announced.

import os
import json
import math
import time
import threading
from collections import deque, Counter

_APP_DIR = "/app" if os.path.isdir("/app") else os.getenv("APP_HOME", ".")
MEMORY_FILE = os.getenv("SQ_MEMORY_FILE", os.path.join(_APP_DIR, ".cache", "memory.json"))

MIRRORED = True        # the laptop/phone camera faces the user: image-left is the user's RIGHT
HFOV_DEG = 62.0        # typical laptop webcam horizontal field of view
DIST_K = 0.9           # metres ~ DIST_K / sqrt(box area fraction); rough monocular estimate


def describe_position(bbox, width, height):
    """bbox (x1,y1,x2,y2) in pixels -> e.g. 'on your left, at table height, close'."""
    if not bbox or not width or not height:
        return "somewhere in view"
    x1, y1, x2, y2 = bbox
    cx = (x1 + x2) / 2 / width
    cy = (y1 + y2) / 2 / height
    if cx < 0.36:
        horiz = "on your right" if MIRRORED else "on your left"
    elif cx > 0.64:
        horiz = "on your left" if MIRRORED else "on your right"
    else:
        horiz = "straight ahead"
    vert = "high up" if cy < 0.33 else "low down, close to you" if cy > 0.67 else "at table height"
    size = (x2 - x1) * (y2 - y1) / (width * height)
    dist = "very close" if size > 0.25 else "close" if size > 0.08 else "further away"
    return f"{horiz}, {vert}, {dist}"


def room_coords(bbox, width, height, view_angle=0.0):
    """bbox -> (angle_deg, distance_m). angle 0 = camera front, positive = to the user's right."""
    if not bbox or not width or not height:
        return view_angle, 2.0
    x1, y1, x2, y2 = bbox
    cx = (x1 + x2) / 2 / width
    off = (cx - 0.5) * HFOV_DEG
    if MIRRORED:
        off = -off
    area = max(1e-4, (x2 - x1) * (y2 - y1) / (width * height))
    dist = max(0.4, min(6.0, DIST_K / math.sqrt(area)))
    ang = (view_angle + off + 180) % 360 - 180
    return round(ang, 1), round(dist, 2)


class WorldState:
    def __init__(self, history_seconds: int = 30, store=None):
        self._lock = threading.Lock()
        self.store = store
        self.history_seconds = history_seconds
        self._events = deque()
        self.current = {}
        self.memory = {}
        self.sensors = {}
        self.last_actions = deque(maxlen=8)
        self.autonomous = False
        self.started = time.time()
        self._frame = None
        self._frame_size = (640, 480)
        self._last_save = 0
        self.view_angle = 0.0           # direction the live camera faces on the room map (deg)
        # presence + change detection
        self.away_after = 30
        self.owner = None
        self.owner_verified_until = 0
        self.present = False
        self.left_at = None
        self._away_baseline = None
        self.last_changes = []
        self._last_frame_at = 0
        self._load()

    # ---------------- persistence
    def _load(self):
        try:
            with open(MEMORY_FILE) as f:
                data = json.load(f)
            self.view_angle = float(data.pop("_view_angle", 0.0))
            for label, m in data.items():
                self.memory[label] = {"last_seen": m["last_seen"], "position": m["position"], "count": m.get("count", 1),
                                      "confidence": m.get("confidence", 0), "ang": m.get("ang", 0.0), "dist": m.get("dist", 2.0),
                                      "trail": deque(m.get("trail", []), maxlen=12), "spots": Counter(m.get("spots", {}))}
        except (OSError, ValueError, KeyError):
            pass

    def _save(self, force=False):
        now = time.time()
        if not force and now - self._last_save < 5:
            return
        self._last_save = now
        try:
            os.makedirs(os.path.dirname(MEMORY_FILE), exist_ok=True)
            data = {label: {**{k: v for k, v in m.items() if k not in ("trail", "spots")},
                            "trail": list(m["trail"]), "spots": dict(m["spots"])} for label, m in self.memory.items()}
            data["_view_angle"] = self.view_angle
            with open(MEMORY_FILE, "w") as f:
                json.dump(data, f)
        except OSError:
            pass

    # ---------------- perception
    def update_detections(self, detections: dict, frame=None, frame_size=None, view_angle=None):
        now = time.time()
        va = self.view_angle if view_angle is None else view_angle
        with self._lock:
            self._last_frame_at = now
            if frame:
                self._frame = bytes(frame)
            if frame_size:
                self._frame_size = frame_size
            w, h = self._frame_size
            self.current = {}
            for label, items in detections.items():
                if not items:
                    continue
                best = max(items, key=lambda d: d.get("confidence", 0.0))
                conf = round(best.get("confidence", 0.0), 2)
                self.current[label] = conf
                self._events.append((now, label, conf))
                pos = describe_position(best.get("bounding_box_xyxy"), w, h)
                ang, dist = room_coords(best.get("bounding_box_xyxy"), w, h, va)
                m = self.memory.setdefault(label, {"count": 0, "trail": deque(maxlen=12), "spots": Counter()})
                m.update({"last_seen": now, "position": pos, "count": len(items), "confidence": conf,
                          "ang": ang, "dist": dist})
                m["spots"][pos] += 1
                if not m["trail"] or abs(m["trail"][-1][0] - ang) > 8 or abs(m["trail"][-1][1] - dist) > 0.5:
                    m["trail"].append([ang, dist, round(now)])
                if self.store:
                    self.store.sighting(label, ang, dist, conf, pos, now)
            self._trim(now)
            event = self._update_presence(now)
            self._save()
        return event

    def update_sensors(self, payload: str):
        parsed = {}
        for part in payload.split(";"):
            if "=" in part:
                k, v = part.split("=", 1)
                try:
                    parsed[k.strip()] = float(v)
                except ValueError:
                    parsed[k.strip()] = v.strip()
        with self._lock:
            self.sensors = parsed

    def record_action(self, action: dict, source: str):
        with self._lock:
            self.last_actions.append({"t": round(time.time() - self.started, 1), "source": source, **action})

    def clear_memory(self):
        with self._lock:
            self.memory = {}
            self.current = {}
            self._events.clear()
            self.last_changes = []
            self._away_baseline = None
            self._save(force=True)

    def set_view_angle(self, deg: float):
        with self._lock:
            self.view_angle = float(deg)
            self._save(force=True)

    def _trim(self, now):
        while self._events and now - self._events[0][0] > self.history_seconds:
            self._events.popleft()

    # ---------------- presence & change detection
    def _update_presence(self, now):
        person_seen = "person" in self.current
        if person_seen and not self.present:
            self.present = True
            if self._away_baseline is not None:
                self.last_changes = self._diff_since_baseline(now)
                self._away_baseline = None
                return "returned"
            return "arrived"
        if not person_seen and self.present:
            last = self.memory.get("person", {}).get("last_seen", now)
            if now - last > self.away_after:
                self.present = False
                self.left_at = last
                self._away_baseline = {label: {"position": m["position"], "ang": m["ang"], "dist": m["dist"], "last_seen": m["last_seen"]}
                                       for label, m in self.memory.items() if label != "person"}
                return "left"
        return None

    def _diff_since_baseline(self, now):
        changes = []
        base = self._away_baseline or {}
        left_at = self.left_at or 0
        for label, m in self.memory.items():
            if label == "person":
                continue
            b = base.get(label)
            if b is None:
                if m["last_seen"] > left_at:
                    changes.append({"object": label, "change": "appeared", "now": m["position"]})
            elif m["last_seen"] > left_at and (m["position"] != b["position"] or abs(m["ang"] - b["ang"]) > 15):
                changes.append({"object": label, "change": "moved", "was": b["position"], "now": m["position"]})
        for label, b in base.items():
            m = self.memory.get(label)
            # only things that were actually around you just before you left (not scans, not old memory)
            recent = b.get("last_seen", 0) >= left_at - 300
            if m and recent and m["last_seen"] <= left_at and label not in self.current:
                changes.append({"object": label, "change": "not seen since you left", "was": b["position"]})
        return changes[:8]

    def tick(self):
        now = time.time()
        with self._lock:
            if self._last_frame_at and now - self._last_frame_at > 10 and self.current:
                self.current = {}
            return self._update_presence(now)

    def presence_view(self):
        now = time.time()
        with self._lock:
            return {"present": self.present,
                    "away_for_s": round(now - self.left_at) if (not self.present and self.left_at) else 0,
                    "owner": self.owner, "owner_verified": now < self.owner_verified_until,
                    "last_changes": self.last_changes}

    def verify_owner(self, minutes=30):
        with self._lock:
            self.owner_verified_until = time.time() + minutes * 60

    # ---------------- views
    def latest_frame(self):
        with self._lock:
            return self._frame

    def memory_view(self, limit: int = 12):
        now = time.time()
        with self._lock:
            rows = []
            for label, m in self.memory.items():
                spots = m.get("spots") or Counter()
                usual, n = (spots.most_common(1)[0] if spots else (m["position"], 1))
                rows.append({"object": label, "position": m["position"], "ang": m.get("ang", 0), "dist": m.get("dist", 2),
                             "last_seen_s_ago": round(now - m["last_seen"]),
                             "visible_now": label in self.current, "count": m["count"],
                             "usual_spot": usual, "sightings": sum(spots.values()) or 1,
                             "usual_share": round(n / max(1, sum(spots.values())), 2),
                             "trail": list(m["trail"])[-8:]})
            rows.sort(key=lambda r: r["last_seen_s_ago"])
            return rows[:limit]

    def detection_summary(self):
        with self._lock:
            cur = dict(self.current)
        return {"current": cur, "memory": self.memory_view(), "view_angle": self.view_angle,
                "t": round(time.time() - self.started, 1)}

    def snapshot(self) -> dict:
        now = time.time()
        with self._lock:
            self._trim(now)
            person_ago = round(now - self.memory["person"]["last_seen"]) if "person" in self.memory else None
            snap = {
                "visible_now": {label: {"confidence": conf, "position": self.memory[label]["position"]}
                                for label, conf in self.current.items()},
                "sensors": dict(self.sensors),
                "person_last_seen_s_ago": person_ago,
                "owner": (self.owner or {}).get("name"),
                "owner_verified": now < self.owner_verified_until,
                "changes_since_owner_left": list(self.last_changes),
                "recent_actions": list(self.last_actions)[-4:],
                "autonomous_mode": self.autonomous,
            }
        snap["object_memory"] = [{k: r[k] for k in ("object", "position", "last_seen_s_ago", "visible_now")}
                                 for r in self.memory_view(8)]
        return snap
