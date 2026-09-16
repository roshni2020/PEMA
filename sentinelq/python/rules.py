# Deterministic safety rules for the assistive use case. They run BEFORE the model and never
# wait on it. The model adds language and judgement; these guarantee the basics.
#
# Each rule returns (actions, reason, say). Actions use the same schema as the LLM (agent.py).
# Labels are COCO classes from the YOLOX brick.

import time

SHARP_OBJECTS = {"knife", "scissors"}
HOT_APPLIANCES = {"oven", "toaster", "microwave"}
TEMP_ALARM_C = 45.0          # from a TMP36 on A0 (reads garbage if none is wired)
DARK_THRESHOLD = 200         # LDR on A1, 12-bit ADC: lower = darker
UNATTENDED_SECONDS = 20      # hazard left out with nobody around for this long

_last_fired = {}


def _cooldown(key, seconds=30):
    now = time.time()
    if now - _last_fired.get(key, 0) < seconds:
        return False
    _last_fired[key] = now
    return True


def evaluate_rules(snapshot: dict):
    visible = snapshot.get("visible_now", {})
    sensors = snapshot.get("sensors", {})
    person_ago = snapshot.get("person_last_seen_s_ago")
    nobody = person_ago is None or person_ago > UNATTENDED_SECONDS
    actions, reasons, say = [], [], []

    # R1: a sharp object is out and nobody is around -> warn (someone with low vision may reach for it)
    sharp = SHARP_OBJECTS & set(visible)
    if sharp and nobody and _cooldown("sharp"):
        obj = sorted(sharp)[0]
        actions += [{"type": "matrix", "icon": "warning"}, {"type": "buzzer", "pattern": "double"}]
        reasons.append(f"{obj} left out, no person for {person_ago}s")
        say.append(f"Careful, a {obj} is out, {visible[obj].get('position', 'in view')}.")

    # R2: hot appliance in view, nobody around, relay (lamp/hot plate) still on -> cut it
    hot = HOT_APPLIANCES & set(visible)
    if hot and nobody and sensors.get("relay") == 1 and _cooldown("hot"):
        actions += [{"type": "relay", "on": False}, {"type": "matrix", "icon": "warning"},
                    {"type": "buzzer", "pattern": "long"}]
        reasons.append(f"{sorted(hot)[0]} unattended with relay on")
        say.append("Nobody is near the stove, so I switched it off.")

    # R3: over-temperature from the MCU sensor -> cut the relay, alarm
    temp = sensors.get("temp_c")
    if isinstance(temp, (int, float)) and temp >= TEMP_ALARM_C and _cooldown("temp"):
        actions += [{"type": "relay", "on": False}, {"type": "matrix", "icon": "warning"},
                    {"type": "buzzer", "pattern": "long"}]
        reasons.append(f"temperature {temp} C over {TEMP_ALARM_C}")
        say.append("It is getting too hot here. I turned the power off.")

    # R4: dark and a person just appeared -> turn the lamp relay on (helps low vision)
    light = sensors.get("light")
    if (isinstance(light, (int, float)) and light < DARK_THRESHOLD and "person" in visible
            and sensors.get("relay") == 0 and _cooldown("dark", 60)):
        actions += [{"type": "relay", "on": True}, {"type": "matrix", "icon": "check"}]
        reasons.append("dark room, person present")
        say.append("It is dark, I turned the light on for you.")

    return actions, "; ".join(reasons), " ".join(say)
