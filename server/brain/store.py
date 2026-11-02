"""Questions about the human's online store (the check_store tool).

A separate program serves the store's facts over HTTP (STORE_API): GET /tools lists its read-only tools in the
Messages API's tool format, POST /call {"name", "input"} runs one. Like web.py, this is a small request of its
own to Anthropic's Messages API, with those tools, that returns a short plain-text answer for the character to
put in its own words. The conversation's model never sees the store's tools, so they cost nothing until asked.

Offered only when STORE_API is set, the provider is Anthropic (LLM_BASE_URL) and the `anthropic` package is
installed. STORE_API_KEY (server/.env) is sent as a bearer token when the store API isn't on this computer.
Read-only by design: it can't change a listing or message anyone.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

from . import config

try:
    import anthropic
except ImportError:  # optional: without it there's no store to ask
    anthropic = None

_SYSTEM = (
    "You answer questions about {human}'s online store for a voice assistant, using the store tools. Reply in at "
    "most three short plain sentences that will be read aloud: the counts, items, dates and dollar amounts that "
    "answer the question. No markdown, no lists, no URLs, no buyer names. The tools only read: if {human} wants "
    "something changed (a listing, a price, a reply to a buyer), say they need to do that at their computer."
)
MAX_ROUNDS = 5  # model calls per question, at most


def available() -> bool:
    return (bool(config.STORE_API) and anthropic is not None
            and "api.anthropic.com" in config.LLM_BASE_URL and bool(os.environ.get("LLM_API_KEY")))


class Store:
    def __init__(self) -> None:
        self.client = anthropic.Anthropic(api_key=os.environ["LLM_API_KEY"], timeout=25.0, max_retries=1)
        self.tools: list[dict] | None = None  # fetched on first use, so a store API started later still works

    def _http(self, path: str, body: dict | None = None) -> dict | list:
        key = os.environ.get("STORE_API_KEY", "").strip()
        req = urllib.request.Request(
            config.STORE_API.rstrip("/") + path, data=None if body is None else json.dumps(body).encode(),
            headers={"Content-Type": "application/json", **({"Authorization": f"Bearer {key}"} if key else {})})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:  # the store API answers errors as JSON too
            try:
                return json.loads(e.read() or b"{}") or {"error": f"HTTP {e.code}"}
            except ValueError:
                return {"error": f"HTTP {e.code}"}

    def ask(self, question: str) -> str:
        """A short answer to `question` about the store. Raises OSError if the store API can't be reached."""
        if self.tools is None:
            tools = self._http("/tools")
            if not isinstance(tools, list):
                raise OSError(f"the store API refused its tool list: {tools.get('error', tools)}")
            self.tools = tools
        system = (time.strftime("It is now %A %d %B %Y, %H:%M local time. ")
                  + _SYSTEM.format(human=config.HUMAN_NAME))
        messages: list[dict] = [{"role": "user", "content": question}]
        used: list[str] = []
        t0 = time.time()
        for _ in range(MAX_ROUNDS):
            r = self.client.messages.create(model=config.MODEL, max_tokens=1024, system=system,
                                            tools=self.tools, messages=messages)
            if r.stop_reason != "tool_use":
                break
            messages.append({"role": "assistant", "content": r.content})
            results = []  # every tool result goes back in one user message
            for block in r.content:
                if block.type != "tool_use":
                    continue
                used.append(block.name)
                out = self._http("/call", {"name": block.name, "input": block.input})
                results.append({"type": "tool_result", "tool_use_id": block.id,
                                "content": json.dumps(out.get("result", out) if isinstance(out, dict) else out),
                                **({"is_error": True} if isinstance(out, dict) and "error" in out else {})})
            messages.append({"role": "user", "content": results})
        text = " ".join(b.text.strip() for b in r.content if b.type == "text").strip()
        print(f"  (store: {', '.join(used) or 'no tools'}, {time.time() - t0:.1f}s)")
        if r.stop_reason == "tool_use" or r.stop_reason == "refusal" or not text:
            return "The store couldn't answer that."
        return " ".join(text.split())
