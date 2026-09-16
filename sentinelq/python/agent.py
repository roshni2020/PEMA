# SentinelQ agents.
#
# Roles (each step is streamed to the dashboard as an "agent_step" event):
#   safety     deterministic rules + action whitelist; runs first and can veto anything (rules.py, actions.py)
#   planner    the LLM on the Qualcomm NPU via GenieX; picks ONE tool per step, loops until it says something
#   vision     the VLM on the NPU: looks at the live camera frame when the planner calls look()
#   memory     the board's object memory (world.py): answers where_is() without any model
#   hardware   Bridge calls to the STM32 (matrix, buzzer, relay, servo, led) through the whitelist
#   speech     what the person hears (browser TTS on the dashboard)
#
# The planner is grammar-constrained (llama.cpp GBNF passed through npu_server.py), so on the
# NPU it can only emit a valid step: {"thought": ..., "tool": <one of 4>, "args": {...}}.

import os
import json
import time
import re
import base64

from arduino.app_utils import Logger

logger = Logger("Agent")

GENIEX_MODEL = os.getenv("GENIEX_MODEL", "")
GENIEX_TIMEOUT = float(os.getenv("GENIEX_TIMEOUT", "120"))
MAX_TOKENS = int(os.getenv("GENIEX_MAX_TOKENS", "160"))
MAX_STEPS = int(os.getenv("SQ_MAX_STEPS", "4"))

_CANDIDATE_URLS = [u for u in [
    os.getenv("GENIEX_URL"),
    "http://10.4.0.102:18182/v1",          # Snapdragon X Elite laptop on the hackathon Wi-Fi (NPU)
    "http://msgpack-rpc-router:18184/v1",  # same laptop over USB (geniex/usb-link.ps1)
    "http://127.0.0.1:18182/v1",
    "http://127.0.0.1:18181/v1",
    "http://host.docker.internal:18181/v1",
    "http://172.17.0.1:18181/v1",
] if u]

SYSTEM_PROMPT = """You are PEMA, a personal environmental memory agent built into an Arduino, for one enrolled
person who has low vision or trouble remembering. You see through a camera, remember where objects were,
notice what changed while they were away, and control a few devices. Every hardware action is verified by
the microcontroller before you may claim it was done. You work in steps. Each step you pick exactly ONE tool:

 where_is  {"object": "<name>"}      -> where that object was last seen, or where a room fixture (door, window,
                                        table, bed, sofa, fridge, stove, sink, tv) is on the scanned room map
 look      {"question": "<text>"}    -> the vision model looks at the live camera frame and answers
 act       {"actions": [ ... ]}      -> hardware: matrix icon, buzzer, relay (lamp/power), servo, led
 say       {"text": "<sentence>"}    -> speak to the person. This ENDS the task. Always finish with say.

Allowed hardware actions:
 {"type":"matrix","icon":"idle|eye|warning|check|heart|off"}   {"type":"buzzer","pattern":"short|double|long|off"}
 {"type":"relay","on":true|false}   {"type":"servo","angle":0-180}   {"type":"led","r":0-255,"g":0-255,"b":0-255}
 {"type":"log","text":"..."}

Guidance:
- If the snapshot has changes_since_owner_left, that is the most important news: say what moved, where it
  was and where it is now, in one or two sentences. Use the owner's name if known.
- "Where is my X" -> where_is first. If it returns a position, answer with say IMMEDIATELY; do not verify
  with look. Only look if where_is has no memory of it.
- "What is in front of me / what do you see / read this" -> look, then say a short, concrete answer.
- Hazards (knife, scissors, stove, heat) -> warn with a buzzer and warning icon, then say it plainly.
- Use look at most once per request, then answer. Do not chain several looks.
- Use plain spoken language, under 25 words, no lists, no emojis. Positions are already phrased for the person.
- Never invent objects that are not in memory, the snapshot, or the look() answer.
- Routine checks with nothing to report: say an empty string ""."""

STEP_GBNF = r'''
root     ::= "{" ws "\"thought\":" ws str "," ws "\"tool\":" ws call "}" ws
call     ::= "\"where_is\"" "," ws "\"args\":" ws "{" ws "\"object\":" ws str "}" ws | "\"look\"" "," ws "\"args\":" ws "{" ws "\"question\":" ws str "}" ws | "\"act\"" "," ws "\"args\":" ws "{" ws "\"actions\":" ws actions "}" ws | "\"say\"" "," ws "\"args\":" ws "{" ws "\"text\":" ws str "}" ws
actions  ::= "[" ws ( action ( "," ws action )* )? "]" ws
action   ::= matrix | buzzer | relay | servo | led | logact
matrix   ::= "{" ws "\"type\":" ws "\"matrix\"" "," ws "\"icon\":" ws ("\"idle\"" | "\"eye\"" | "\"warning\"" | "\"check\"" | "\"heart\"" | "\"off\"") ws "}" ws
buzzer   ::= "{" ws "\"type\":" ws "\"buzzer\"" "," ws "\"pattern\":" ws ("\"short\"" | "\"double\"" | "\"long\"" | "\"off\"") ws "}" ws
relay    ::= "{" ws "\"type\":" ws "\"relay\"" "," ws "\"on\":" ws ("true" | "false") ws "}" ws
servo    ::= "{" ws "\"type\":" ws "\"servo\"" "," ws "\"angle\":" ws int ws "}" ws
led      ::= "{" ws "\"type\":" ws "\"led\"" "," ws "\"r\":" ws int "," ws "\"g\":" ws int "," ws "\"b\":" ws int ws "}" ws
logact   ::= "{" ws "\"type\":" ws "\"log\"" "," ws "\"text\":" ws str ws "}" ws
str      ::= "\"" ( [^"\\\x7F\x00-\x1F] | "\\" (["\\bfnrt] | "u" [0-9a-fA-F]{4}) ){0,240} "\"" ws
int      ::= [0-9]{1,3}
ws       ::= [ \t\n]{0,4}
'''

VISION_PROMPT = ("You are the eyes of an assistant for a person with low vision. Answer the question about the "
                 "camera image in one or two short, concrete sentences. Mention positions as left/right/ahead "
                 "from the camera's point of view and any hazard you notice.")


class Agent:
    def __init__(self, ui, world, executor):
        self.ui = ui
        self.world = world
        self.executor = executor
        self.client = None
        self.base_url = None
        self.backend = "none"
        self.models = []
        self.model = GENIEX_MODEL
        self.has_vlm = False
        self.stats = {"requests": 0, "steps": 0, "prompt_tokens": 0, "generated_tokens": 0, "last": None}
        self.roommap = None          # set by main.py; gives where_is() access to scanned fixtures
        self._connect()

    def vision_text(self, jpeg: bytes, question: str) -> str:
        """Ask the vision model on the NPU about an image (used by the room scan for fixtures)."""
        if self.backend != "geniex":
            self._connect()
        if self.backend != "geniex" or not self.has_vlm:
            return ""
        b64 = base64.b64encode(jpeg).decode()
        resp = self.client.chat.completions.create(
            model=self.model, max_tokens=60, temperature=0.1,
            messages=[{"role": "user", "content": [
                {"type": "text", "text": question},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}]}])
        self._account((resp.model_extra or {}).get("geniex_profile"))
        return (resp.choices[0].message.content or "").strip()

    # ---------------- connection
    def _connect(self):
        try:
            from openai import OpenAI
        except ImportError:
            logger.warning("openai package missing; add it to python/requirements.txt")
            return
        for url in _CANDIDATE_URLS:
            try:
                probe = OpenAI(base_url=url, api_key="geniex", timeout=3, max_retries=0)
                listed = probe.models.list().data
                self.models = [m.id for m in listed]
                self.client = OpenAI(base_url=url, api_key="geniex", timeout=GENIEX_TIMEOUT, max_retries=0)
                self.base_url, self.backend = url, "geniex"
                if not self.model:
                    self.model = self.models[0] if self.models else "default"
                self.has_vlm = any(getattr(m, "model_extra", {}).get("vision") or "vl" in m.id.lower() for m in listed)
                logger.info(f"GenieX at {url}; models={self.models}; vlm={self.has_vlm}")
                return
            except Exception as e:
                logger.info(f"GenieX not at {url}: {e}")
        logger.error("No GenieX server reachable; the planner is offline")

    def describe(self):
        return {"backend": self.backend, "url": self.base_url, "model": self.model, "vlm": self.has_vlm,
                "profile": self.stats["last"], "stats": self.stats}

    # ---------------- event helper
    def _step(self, role, text, **extra):
        payload = {"role": role, "text": text, "t": round(time.time(), 2), **extra}
        self.ui.send_message("agent_step", payload)
        return payload

    # ---------------- the loop
    def run(self, command: str, source: str = "dashboard"):
        """Full agentic cycle for one command. Returns the final spoken text ('' if nothing)."""
        snapshot = self.world.snapshot()

        # 1. safety agent (deterministic) always runs first
        from rules import evaluate_rules
        rule_actions, rule_reason, rule_say = evaluate_rules(snapshot)
        if rule_actions:
            self._step("safety", f"rule fired: {rule_reason}", actions=rule_actions)
            self.executor.run(rule_actions, reason=f"[rule] {rule_reason}", source="safety", say=rule_say)
        else:
            self._step("safety", "no rule triggered")

        if self.backend != "geniex":
            self._connect()                      # server may have been busy or down at startup; retry now
            self.ui.send_message("status", {"state": "thinking", "llm": self.describe()})
        if self.backend != "geniex":
            self._step("planner", "GenieX server not reachable, cannot plan", error=True)
            return ""

        # 2. planner loop
        history = [{"role": "system", "content": SYSTEM_PROMPT},
                   {"role": "user", "content": f"SNAPSHOT:\n{json.dumps(snapshot, separators=(',', ':'))}\n\nREQUEST: {command}"}]
        final = ""
        seen_calls = set()
        useful = []                       # tool results worth speaking if the planner stalls
        for i in range(MAX_STEPS):
            step, profile = self._plan(history)
            if step is None:
                self._step("planner", "planner returned nothing usable", error=True)
                break
            tool, args = step.get("tool"), step.get("args") or {}
            key = json.dumps([tool, args], sort_keys=True)
            if tool != "say" and key in seen_calls:
                # the small model is looping on the same call: answer from what it already found
                final = (useful[-1] if useful else "I am not sure. Please ask again.")[:200]
                self.executor.run([], reason="", source="planner", say=final)
                self._step("speech", final + "  (planner repeated itself; answered from memory)")
                break
            seen_calls.add(key)
            self._step("planner", step.get("thought", ""), tool=tool, args=args, profile=profile, n=i + 1)
            history.append({"role": "assistant", "content": json.dumps(step)})

            if tool == "say":
                final = str(args.get("text", ""))[:240]
                if final.strip():
                    # highlight the objects the answer talks about on the room map
                    names = [r["object"] for r in self.world.memory_view(50) if r["object"] in final.lower()]
                    if self.roommap:
                        names += [f for f in self.roommap.view().get("fixtures", {}) if f in final.lower()]
                    if names:
                        self.ui.send_message("highlight", {"objects": names})
                    self.executor.run([], reason="", source="planner", say=final)
                    self._step("speech", final)
                else:
                    self._step("speech", "(nothing to report)")
                break
            elif tool == "where_is":
                result = self._where_is(str(args.get("object", "")))
                self._step("memory", result)
                if not result.startswith("no memory"):
                    useful.append(result)
            elif tool == "look":
                if any(h["content"].startswith("TOOL RESULT (look)") for h in history if h["role"] == "user"):
                    result = "you already looked once; answer now with say."
                else:
                    result, vprof = self._look(str(args.get("question", "What is in front of me?")))
                    self._step("vision", result, profile=vprof)
                    if not result.startswith(("no camera", "vision error", "vision model")):
                        useful.append(result)
            elif tool == "act":
                acts = args.get("actions") or []
                res = self.executor.run(acts, reason=step.get("thought", ""), source="planner")
                parts = []
                for r in res:
                    a = r["action"]
                    label = a.get("type", "?") + ("=" + str(a.get("icon") or a.get("pattern") or a.get("on") or a.get("angle") or "") if isinstance(a, dict) else "")
                    parts.append(f"{label} {'VERIFIED' if r.get('verified') else 'NOT verified'} ({r.get('detail') or r.get('error')})")
                result = "; ".join(parts) or "no actions"
                self._step("hardware", result, actions=acts, verified=all(r.get("verified") for r in res) if res else False)
            else:
                result = f"unknown tool {tool}"
            history.append({"role": "user", "content": f"TOOL RESULT ({tool}): {result}\nContinue. Finish with say."})
        else:
            # ran out of steps without say: answer from what the tools already found
            final = (useful[-1] if useful else "I am not sure. Please ask again.")[:200]
            self.executor.run([], reason="", source="planner", say=final)
            self._step("speech", final + "  (step limit reached)")
        self.stats["requests"] += 1
        return final

    # ---------------- tools
    def _where_is(self, name: str) -> str:
        name = name.lower().strip()
        self.ui.send_message("highlight", {"object": name})
        if self.roommap:
            fx = self.roommap.where_is_fixture(name)
            if fx:
                return fx
        rows = self.world.memory_view(50)
        hit = next((r for r in rows if r["object"] == name), None) or \
              next((r for r in rows if name and (name in r["object"] or r["object"] in name)), None)
        if not hit:
            seen = ", ".join(r["object"] for r in rows[:8]) or "nothing yet"
            return f"no memory of '{name}'. Objects seen so far: {seen}."
        ago = hit["last_seen_s_ago"]
        when = "right now" if hit["visible_now"] else (f"{ago} seconds ago" if ago < 120 else f"{ago // 60} minutes ago")
        out = f"{hit['object']}: {hit['position']}, seen {when}."
        # learned over time: where this object usually is (frequency of past sightings)
        if not hit["visible_now"] and hit.get("sightings", 0) >= 5 and hit.get("usual_spot") != hit["position"]:
            out += f" It is usually {hit['usual_spot']} ({int(hit['usual_share'] * 100)}% of {hit['sightings']} sightings)."
        return out

    def _look(self, question: str):
        frame = self.world.latest_frame()
        if not frame:
            return "no camera frame available (camera off?)", None
        if not self.has_vlm:
            cur = self.world.detection_summary().get("current", {})
            return "vision model not loaded; detector sees: " + (", ".join(cur) or "nothing"), None
        b64 = base64.b64encode(frame).decode()
        try:
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "system", "content": VISION_PROMPT},
                          {"role": "user", "content": [
                              {"type": "text", "text": question},
                              {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}]}],
                max_tokens=90, temperature=0.2)
            prof = (resp.model_extra or {}).get("geniex_profile")
            self._account(prof)
            return (resp.choices[0].message.content or "").strip()[:400], prof
        except Exception as e:
            return f"vision error: {e}", None

    def _plan(self, history):
        try:
            resp = self.client.chat.completions.create(
                model=self.model, messages=history, max_tokens=MAX_TOKENS, temperature=0.2,
                extra_body={"response_format": {"type": "json_object"}, "grammar": STEP_GBNF})
        except Exception as e:
            logger.error(f"planner call failed: {e}")
            self.ui.send_message("agent", {"error": str(e)})
            return None, None
        prof = (resp.model_extra or {}).get("geniex_profile")
        self._account(prof)
        return self._parse(resp.choices[0].message.content or ""), prof

    def _account(self, prof):
        if not prof:
            return
        self.stats["steps"] += 1
        self.stats["prompt_tokens"] += int(prof.get("prompt_tokens") or 0)
        self.stats["generated_tokens"] += int(prof.get("generated_tokens") or 0)
        self.stats["last"] = prof
        self.ui.send_message("inference", {"last": prof, "stats": self.stats})

    @staticmethod
    def _parse(raw: str):
        text = re.sub(r"<think>.*?</think>", "", raw, flags=re.S)
        text = re.sub(r'([,{]\s*)""([A-Za-z_]+)"\s*:', r'\1"\2":', text)
        start = text.find("{")
        if start < 0:
            return None
        try:
            obj, _ = json.JSONDecoder().raw_decode(text[start:])
        except json.JSONDecodeError:
            return None
        return obj if isinstance(obj, dict) and obj.get("tool") else None
