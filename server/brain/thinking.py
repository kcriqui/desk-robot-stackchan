"""Thinking: sends a question (and the newest camera frame) to the language
model and hands back what Rocky should say and feel, one sentence at a time
as the model writes it.

The request is the OpenAI-style chat API, which OpenRouter, Anthropic and
OpenAI all serve, so the provider is a base URL and the model is a string
(config.LLM_BASE_URL / config.MODEL; the key is LLM_API_KEY in server/.env).

Rocky has real abilities the model can call (tool use): `look` moves
the head and comes back with a fresh camera frame from the new angle,
`track_face` starts/stops following the human, `check_temperature` reads
his chip's temperature, `set_timer` / `cancel_timer` manage reminders he
announces himself, `remember` / `forget` keep facts in his long-term
memory (memories.py), `web_search` and `check_weather` look things up
(web.py, weather.py), `dance` dances (dance.py), and on a StackChan
`check_battery` reads his battery.
main.py supplies the functions that actually do those things; only the
tools it supplies are offered.
"""

from __future__ import annotations

import base64
import json
import os
import re
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass

import openai

from . import config
from . import personality

_EMOTION_TAG = re.compile(r"^\s*\[(\w+)\]\s*", re.S)
# A sentence ends at . ! or ? followed by a space — but not at an ellipsis:
# "just... clear answer." is one sentence, not two.
_SENTENCE_END = re.compile(r"(?<=[!?])\s+|(?<=[^.]\.)\s+")

# An action returns (text for the model, optional fresh camera JPEG), and may add
# True as a third item: done, the turn ends there with no more words from the
# model (a dance speaks for itself).
Action = Callable[[dict], tuple]

# The neck tilts one way from level: down on the original build, up on a
# StackChan (config.BODY).
_NOD = config.NOD
_NO_NOD = "up" if _NOD == "down" else "down"
_NECK_LIMIT = (
    f"{_NO_NOD.capitalize()} is as {'high' if _NO_NOD == 'up' else 'low'} as 'level': "
    f"your neck cannot tilt {'above' if _NO_NOD == 'up' else 'below'} eye level."
)
_NOD_MAX = int(max(abs(config.TRACK_TILT_MIN), abs(config.TRACK_TILT_MAX)))
_PAN_MAX = int(config.TRACK_PAN_LIMIT)
_CENTER = ("straight ahead and level" if not config.TILT_REST else
           f"straight ahead, tilted {config.TILT_REST:.0f} deg up (your resting pose)")

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "look",
            "description": (
                "Get a fresh camera picture, moving your head first if asked. Use "
                "direction \"here\" (no movement) when your human is showing you "
                "something or you just need a newer picture: they are usually "
                "already in front of you. Use left/right/down/up/around when asked "
                "to look somewhere, or to see something outside the current picture; "
                "always call it when asked, even if you think you are already there: "
                "the result tells you where your head really is and whether it is at "
                "a limit. Describe only what the fresh image shows. "
                f"{_NECK_LIMIT}"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "direction": {
                        "type": "string",
                        "enum": ["here", "left", "right", _NOD, "level", "center"],
                        "description": (
                            "here = don't move, just a fresh picture; "
                            f"left/right turn the head only (no nod); {_NOD}/level nod only (no turn); "
                            f"center = {_CENTER}"
                        ),
                    },
                    "degrees": {
                        "type": "number",
                        "description": (
                            f"How far: 5-{_PAN_MAX} for left/right, 5-{_NOD_MAX} for {_NOD}. "
                            "Omit for a normal look."
                        ),
                    },
                },
                "required": ["direction"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "track_face",
            "description": "Start or stop following the human's face with your head.",
            "parameters": {
                "type": "object",
                "properties": {"on": {"type": "boolean"}},
                "required": ["on"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_battery",
            "description": (
                "Read your battery: roughly how full it is, whether you are charging "
                "or on USB power, and about how long it will last. Call it whenever "
                "asked about your battery, charge, power or how long you can run."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_temperature",
            "description": (
                "Read the temperature of the chip inside your head (not the room's: you "
                "have no room thermometer). Call it when asked how hot or warm you are."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_timer",
            "description": (
                "Set a timer or reminder; when it's due you announce it out loud. For a "
                "clock time (\"at 6\"), work out the minutes from the current time you are "
                "given. Always call it when asked; don't just promise to remember."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "minutes": {"type": "number", "description": "From now; may be fractional (0.5 = 30 s). Up to 1440."},
                    "about": {"type": "string", "description": "What it's for, in a few words (\"tea\", \"call mom\"); empty for a plain timer."},
                },
                "required": ["minutes"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cancel_timer",
            "description": "Cancel a timer you set, by what it's for, or \"all\".",
            "parameters": {
                "type": "object",
                "properties": {"about": {"type": "string"}},
                "required": ["about"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "Look something up on the internet: news, prices, scores, opening hours, people, "
                "anything current or that you don't know for sure. Ask one clear question in "
                "plain words, with the place and time when they matter (\"Apple stock price "
                "today\", \"who won the Seahawks game last night\"). Takes a few seconds. For the "
                "weather, use check_weather instead."
            ),
            "parameters": {
                "type": "object",
                "properties": {"question": {"type": "string"}},
                "required": ["question"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_weather",
            "description": (
                "The weather right now and for the next three days, live. With no place it's for "
                "your human's home; otherwise name the place (\"Seattle, WA\", \"Paris, France\")."
            ),
            "parameters": {
                "type": "object",
                "properties": {"place": {"type": "string", "description": "Omit for home."}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "dance",
            "description": (
                "Dance: one of your four short head-dance routines (about 15 s), picked at random. Use it when "
                "asked to dance. Say a short line first (\"Dance time!\"); after it you say nothing more."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remember",
            "description": (
                "Keep a fact in your long-term memory, so you still know it in later "
                "conversations and after a restart. One short, self-contained fact in plain "
                f"words, naming who it's about (\"{config.HUMAN_NAME}'s cats are Milo and "
                "Luna\"). If it replaces something you remember, forget the old one first."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "fact": {"type": "string", "description": "The fact, under 200 characters."},
                    "days": {"type": "number", "description": (
                        "Only for facts that stop being true (\"has a cold\": 7, \"guests this "
                        "weekend\": 3). Omit for lasting facts.")},
                },
                "required": ["fact"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "forget",
            "description": (
                "Remove facts from your long-term memory: a few words from the fact, its "
                "number, or \"all\" (only when your human clearly asks to forget everything)."
            ),
            "parameters": {
                "type": "object",
                "properties": {"about": {"type": "string"}},
                "required": ["about"],
            },
        },
    },
]


@dataclass
class Reply:
    text: str
    emotion: str


class Interrupted(Exception):
    """The human started talking again; drop this reply."""


def _image_part(jpeg: bytes) -> dict:
    data = base64.standard_b64encode(jpeg).decode()
    return {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{data}"}}


def _repair(messages: list[dict]) -> list[dict]:
    """The history with any broken tool exchange left out: a tool result
    whose call isn't right before it, or a call missing a result. The API
    refuses either (400), and would keep refusing every later request."""
    out: list[dict] = []
    i = 0
    while i < len(messages):
        m = messages[i]
        if m.get("role") == "tool":
            i += 1  # an orphan: its call isn't directly before it
            continue
        if m.get("role") == "assistant" and m.get("tool_calls"):
            ids = {c["id"] for c in m["tool_calls"]}
            j = i + 1
            results = []
            while j < len(messages) and messages[j].get("role") == "tool":
                results.append(messages[j])
                j += 1
            if {r.get("tool_call_id") for r in results} >= ids:
                out.append(m)
                out.extend(r for r in results if r.get("tool_call_id") in ids)
            elif m.get("content"):
                out.append({"role": "assistant", "content": m["content"]})
            i = j
            continue
        out.append(m)
        i += 1
    return out


class RobotBrain:
    def __init__(self, actions: dict[str, Action] | None = None) -> None:
        self.client = openai.OpenAI(
            base_url=config.LLM_BASE_URL,
            api_key=os.environ.get("LLM_API_KEY", "missing"),
            default_headers={"X-Title": "desk-robot"},  # shows up in OpenRouter's usage page; others ignore it
        )
        self.history: list[dict] = []
        self.actions = actions or {}
        # Returns a line on his current state (e.g. whether he's tracking),
        # added to every question: some changes never pass through the
        # model (the "track me" shortcut, falling asleep), so it can't
        # work them out from the conversation.
        self.situation: Callable[[], str] | None = None
        # Returns his long-term memory (memories.py) for the system prompt, or
        # "" when he remembers nothing yet.
        self.memory: Callable[[], str] | None = None
        self.emotion = "neutral"        # emotion of the reply in progress
        # The turn being answered: its messages (the question, any tool calls
        # and results) go into history only when it finishes. Abandoned or
        # failed turns leave nothing behind; a half turn (a tool result whose
        # call was cut) gets every later request refused with a 400.
        self._inflight: list[dict] | None = None

    def ask(self, question: str, jpeg: bytes | None = None, camera_wanted: bool = False) -> Reply:
        """The whole reply at once. See reply() for the streaming form."""
        sentences = list(self.reply(question, jpeg, camera_wanted=camera_wanted))
        return Reply(" ".join(sentences), self.emotion)

    def reply(
        self,
        question: str,
        jpeg: bytes | None = None,
        on_emotion: Callable[[str], None] | None = None,
        cancelled: threading.Event | None = None,
        camera_wanted: bool = False,
    ) -> Iterator[str]:
        """Rocky's reply, one sentence at a time as the model writes it.

        on_emotion(name) is called as soon as the emotion tag at the start of
        the reply is known — before the first sentence — so the face can
        change while he's still composing. Set `cancelled`, or close the
        generator early, and the question is dropped from his memory as if it
        was never asked. `camera_wanted` with no `jpeg` means the question
        was about seeing but the camera had no fresh picture; he's told so,
        otherwise he answers from memory and claims to see things.
        """
        content: list[dict] = [{"type": "text", "text": question}]
        if jpeg is not None:
            content.append({"type": "text", "text": "(Live picture from your camera, taken just now, because the question seems to be about what you can see. If it isn't, ignore the picture.)"})
            content.append(_image_part(jpeg))
        elif camera_wanted:
            content.append({"type": "text", "text": "(Your camera has no fresh picture right now, so you cannot see anything at the moment.)"})
        if self.situation is not None:
            content.append({"type": "text", "text": f"({self.situation()})"})
        turn: list[dict] = [{"role": "user", "content": content}]
        self._inflight = turn
        self.emotion = "neutral"
        spoken: list[str] = []
        error: tuple[str, str] | None = None

        gen = self._converse(turn, on_emotion, cancelled or threading.Event())
        try:
            for sentence in gen:
                spoken.append(sentence)
                yield sentence
        except (Interrupted, GeneratorExit):
            gen.close()
            if self._inflight is turn:
                self._inflight = None
            raise
        except openai.APIConnectionError:
            error = ("Brain cannot reach internet. Bad bad bad.", "sad")
        except openai.AuthenticationError:
            error = ("Brain has no key. Set LLM_API_KEY, human.", "sad")
        except openai.APIStatusError as e:
            print(f"  (API error {e.status_code}: {getattr(e, 'message', e)})")
            error = (f"Ow. Brain hurts. API error {e.status_code}.", "sad")
        finally:
            gen.close()

        if error is not None:
            if self._inflight is turn:
                self._inflight = None
            self.emotion = error[1]
            if on_emotion is not None:
                on_emotion(self.emotion)
            yield error[0]
            return

        if self._inflight is not turn:
            return  # abandoned while it ran (the human kept talking): keep none of it
        self._inflight = None
        self.history.extend(turn)
        if spoken:
            # Remember what was actually said, so he can't refer back to a
            # part that got cut.
            self.history.append({"role": "assistant", "content": f"[{self.emotion}] {' '.join(spoken)}"})
        self._strip_images()
        self._trim_history()

    def abandon(self) -> None:
        """Forget the question being answered right now (the human kept
        talking). Its turn is never added to the history, even if the model
        call finishes after this."""
        self._inflight = None

    def _converse(self, turn: list[dict], on_emotion: Callable[[str], None] | None,
                  cancelled: threading.Event) -> Iterator[str]:
        """One question, possibly several model calls if it uses its abilities.
        Yields sentences as they complete. The turn's messages go in `turn`,
        not the history (see reply())."""
        nudged = False
        limit = config.REPLY_MAX_SENTENCES
        tools = [t for t in TOOLS if t["function"]["name"] in self.actions]
        for _ in range(5):
            if cancelled.is_set():
                raise Interrupted()
            # Read each round: a fact he just remembered counts straight away.
            known = self.memory() if self.memory is not None else ""
            system = personality.SYSTEM_PROMPT + (f"\n\n{known}" if known else "")
            stream = self.client.chat.completions.create(
                model=config.MODEL,
                max_tokens=config.LLM_MAX_TOKENS,  # backstop; the sentence limit does the real work
                messages=[{"role": "system", "content": system},
                          *_repair(self.history), *turn],
                tools=tools or openai.NOT_GIVEN,
                stream=True,
            )
            buf = ""                 # text not yet released as a sentence
            raw: list[str] = []      # everything the model wrote this round
            tag_decided = False
            calls: dict[int, dict] = {}
            spoken = 0
            try:
                for chunk in stream:
                    if cancelled.is_set():
                        raise Interrupted()
                    if not chunk.choices:
                        continue
                    delta = chunk.choices[0].delta
                    for tc in delta.tool_calls or []:
                        entry = calls.setdefault(tc.index, {"id": "", "name": "", "arguments": ""})
                        if tc.id:
                            entry["id"] = tc.id
                        if tc.function is not None:
                            if tc.function.name:
                                entry["name"] = tc.function.name
                            if tc.function.arguments:
                                entry["arguments"] += tc.function.arguments
                    if not delta.content:
                        continue
                    raw.append(delta.content)
                    buf += delta.content
                    if not tag_decided:
                        buf, tag_decided = self._take_emotion_tag(buf, final=False)
                        if not tag_decided:
                            continue
                        if on_emotion is not None:
                            on_emotion(self.emotion)
                    parts = _SENTENCE_END.split(buf)
                    while len(parts) > 1:  # everything but the last piece is a whole sentence
                        s = parts.pop(0).strip()
                        if s:
                            spoken += 1
                            yield s
                        if spoken >= limit:
                            break
                    buf = parts[-1] if parts else ""
                    if spoken >= limit:
                        print(f"  (trimmed reply to {limit} sentences)")
                        buf = ""
                        break
            finally:
                stream.close()

            if not tag_decided:
                buf, _ = self._take_emotion_tag(buf, final=True)
                if on_emotion is not None and (buf.strip() or not calls):
                    on_emotion(self.emotion)

            if calls:
                # He decided to do something: say any lead-in, run it, tell him what happened.
                if buf.strip():
                    yield buf.strip()
                    buf = ""
                for c in calls.values():
                    # A reply cut short mid-call leaves "" or half a JSON
                    # object; sending that back gets the next request refused.
                    try:
                        c["args"] = json.loads(c["arguments"] or "{}")
                    except json.JSONDecodeError:
                        c["args"] = {}
                    if not isinstance(c["args"], dict):
                        c["args"] = {}
                turn.append({
                    "role": "assistant",
                    "content": "".join(raw),
                    "tool_calls": [
                        {"id": c["id"], "type": "function",
                         "function": {"name": c["name"], "arguments": json.dumps(c["args"])}}
                        for _, c in sorted(calls.items())
                    ],
                })
                fresh: bytes | None = None
                done = False
                for _, c in sorted(calls.items()):
                    if cancelled.is_set():
                        raise Interrupted()
                    args = c["args"]
                    action = self.actions.get(c["name"])
                    if action is None:
                        text = f"unknown ability {c['name']}"
                    else:
                        try:
                            result = action(args)
                            text, img = result[0], result[1]
                            done = done or (len(result) > 2 and bool(result[2]))
                        except Exception as e:  # the robot didn't cooperate; say so
                            text, img = f"could not do that: {e}", None
                        if img:
                            fresh = img
                    print(f"  [{c['name']} {args} -> {text}]")
                    turn.append({"role": "tool", "tool_call_id": c["id"], "content": text})
                if done:
                    return  # the action is the answer (his lead-in, if any, was already said)
                if fresh is not None:
                    turn.append({
                        "role": "user",
                        "content": [{"type": "text", "text": "Camera view after moving:"}, _image_part(fresh)],
                    })
                continue

            if not "".join(raw).strip() and not nudged:
                # Some models go quiet right after using an ability. Ask once.
                nudged = True
                turn.append({"role": "user", "content": "(Tell your human what you just did, in one short line.)"})
                continue
            if nudged and turn and turn[-1].get("role") == "user":
                turn.pop()  # don't keep the nudge in the transcript
            tail = buf.strip()
            if tail and spoken < limit:
                yield tail
            elif not spoken and not tail:
                self.emotion = "thinking"
                if on_emotion is not None:
                    on_emotion(self.emotion)
                yield "Hmm. Words did not come. Ask again."
            return

        self.emotion = "thinking"
        if on_emotion is not None:
            on_emotion(self.emotion)
        yield "Too many things at once. Ask again, human."

    def _take_emotion_tag(self, buf: str, final: bool) -> tuple[str, bool]:
        """Look for "[happy] " at the start of the reply. Returns (text with the
        tag removed, decided). Not decided means: need more text to know."""
        lead = buf.lstrip()
        if not lead:
            return buf, final
        if not lead.startswith("["):
            return buf, True
        if "]" in lead:
            m = _EMOTION_TAG.match(lead)
            if m:
                candidate = m.group(1).lower()
                if candidate in config.EMOTIONS:
                    self.emotion = candidate
                return _EMOTION_TAG.sub("", lead, count=1), True
            return buf, True
        if final or len(lead) > 24:
            return buf, True  # a "[" with no "]" in sight: not a tag
        return buf, False

    def _strip_images(self) -> None:
        """Replace old camera frames with a note. Keeping every image in the
        history would make each later question cost far more."""
        for m in self.history:
            if m.get("role") == "user" and isinstance(m.get("content"), list):
                parts = [p for p in m["content"] if p.get("type") == "text"]
                if len(parts) != len(m["content"]):
                    parts.append({"type": "text", "text": "(a camera image was attached here)"})
                m["content"] = parts

    def _trim_history(self) -> None:
        max_msgs = config.MAX_HISTORY_TURNS * 2
        if len(self.history) > max_msgs:
            cut = len(self.history) - max_msgs
            # Never start the history on a tool result: back up to a user turn.
            while cut < len(self.history) and self.history[cut].get("role") != "user":
                cut += 1
            del self.history[:cut]
