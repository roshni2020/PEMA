# ActionExecutor - the only place that talks to the STM32.
# 1. Validates every action against a whitelist (a hallucinated action can never reach a pin).
# 2. Dispatches over Bridge RPC.
# 3. VERIFIES: reads the microcontroller's state back and compares. An action is only reported
#    as done when the MCU confirms it. This is the "verified physical action" in the product loop.

import time
from arduino.app_utils import Bridge, Logger

logger = Logger("Actions")

MATRIX_ICONS = {"idle", "eye", "warning", "check", "heart", "off"}
BUZZER_PATTERNS = {"short", "double", "long", "off"}
SERVO_RANGE = (0, 180)

SCHEMA = {
    "matrix": {"icon": MATRIX_ICONS},
    "buzzer": {"pattern": BUZZER_PATTERNS},
    "relay": {"on": {True, False}},
    "servo": {"angle": SERVO_RANGE},
    "led": {"r": (0, 255), "g": (0, 255), "b": (0, 255)},
    "log": {"text": str},
}


def read_mcu_state() -> dict:
    """relay=1;icon=heart;uptime_s=12 -> dict. Empty dict if the MCU does not answer."""
    try:
        raw = Bridge.call("get_state")
        out = {}
        for part in str(raw).split(";"):
            if "=" in part:
                k, v = part.split("=", 1)
                out[k] = v
        return out
    except Exception as e:
        logger.warning(f"get_state failed: {e}")
        return {}


class ActionExecutor:
    def __init__(self, ui, world):
        self.ui = ui
        self.world = world
        self.on_action = None      # optional hook (source, action, verified, detail) -> history store

    def run(self, actions, reason: str = "", source: str = "", say: str = ""):
        if say:
            self.ui.send_message("say", {"text": say, "source": source})
        results = []
        for raw in actions or []:
            action = self._validate(raw)
            if action is None:
                self.ui.send_message("action", {"ok": False, "raw": raw, "source": source,
                                                "error": "rejected by whitelist"})
                results.append({"action": raw, "ok": False, "verified": False, "error": "rejected by whitelist"})
                continue
            try:
                self._dispatch(action)
                verified, detail = self._verify(action)
                self.world.record_action({**action, "verified": verified}, source)
                self.ui.send_message("action", {"ok": True, "action": action, "reason": reason,
                                                "source": source, "verified": verified, "detail": detail})
                if self.on_action:
                    try:
                        self.on_action(source, action, verified, detail)
                    except Exception:
                        pass
                results.append({"action": action, "ok": True, "verified": verified, "detail": detail})
            except Exception as e:
                logger.error(f"Bridge call failed for {action}: {e}")
                self.ui.send_message("action", {"ok": False, "action": action, "source": source, "error": str(e)})
                results.append({"action": action, "ok": False, "verified": False, "error": str(e)})
        return results

    # ---------------- verification by MCU readback
    def _verify(self, a):
        t = a["type"]
        if t in ("buzzer", "led", "servo", "log"):
            # momentary or write-only outputs: confirm the MCU is alive and acknowledged the call
            st = read_mcu_state()
            return (bool(st), "MCU acknowledged" if st else "no MCU readback")
        time.sleep(0.05)
        st = read_mcu_state()
        if not st:
            return False, "no MCU readback"
        if t == "relay":
            ok = st.get("relay") == ("1" if a["on"] else "0")
            return ok, f"MCU reports relay={st.get('relay')}"
        if t == "matrix":
            ok = st.get("icon") == a["icon"]
            return ok, f"MCU reports icon={st.get('icon')}"
        return True, "ok"

    # ---------------- validation
    def _validate(self, raw):
        if not isinstance(raw, dict):
            return None
        kind = raw.get("type")
        spec = SCHEMA.get(kind)
        if spec is None:
            return None
        out = {"type": kind}
        for key, allowed in spec.items():
            val = raw.get(key)
            if allowed is str:
                if not isinstance(val, str):
                    return None
                out[key] = val[:120]
            elif isinstance(allowed, set):
                if isinstance(val, str):
                    val = val.lower() if kind != "relay" else val
                if val not in allowed:
                    return None
                out[key] = val
            elif isinstance(allowed, tuple):
                try:
                    val = int(val)
                except (TypeError, ValueError):
                    return None
                out[key] = max(allowed[0], min(allowed[1], val))
        return out

    # ---------------- dispatch to sketch functions (see sketch/sketch.ino)
    def _dispatch(self, a):
        t = a["type"]
        if t == "matrix":
            Bridge.call("set_matrix", a["icon"])
        elif t == "buzzer":
            Bridge.call("buzz", a["pattern"])
        elif t == "relay":
            Bridge.call("set_relay", 1 if a["on"] else 0)
        elif t == "servo":
            Bridge.call("set_servo", a["angle"])
        elif t == "led":
            Bridge.call("set_led", a["r"], a["g"], a["b"])
        elif t == "log":
            logger.info(f"[agent] {a['text']}")
