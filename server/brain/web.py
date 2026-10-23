"""Looking things up on the internet (the web_search tool).

The conversation goes through the OpenAI-style chat API, which can't carry
Anthropic's server-side web search. So this is a separate, small request to
Anthropic's own Messages API, with the web search tool on. It's the same
model and the same key (LLM_API_KEY), and it returns a short plain-text
answer for Rocky to put in his own words.

It's offered only when the provider is Anthropic (LLM_BASE_URL) and the
`anthropic` package is installed. Cost: $10 per 1,000 searches plus the
model reading the results, about 1.2 cents per question.
"""

from __future__ import annotations

import os
import time

from . import config, weather

try:
    import anthropic
except ImportError:  # optional: without it he just can't search
    anthropic = None

_SYSTEM = (
    "You look things up for a voice assistant. Use web search, then reply in at most two short "
    "plain sentences that will be read aloud: the key facts and numbers with units, and when the "
    "information is from if it may have changed (\"as of 2 PM\"). No markdown, no URLs, no lists. "
    "If you could not find it, say so in one sentence."
)


def available() -> bool:
    return (config.WEB_SEARCH and anthropic is not None
            and "api.anthropic.com" in config.LLM_BASE_URL and bool(os.environ.get("LLM_API_KEY")))


class WebSearch:
    def __init__(self) -> None:
        self.client = anthropic.Anthropic(api_key=os.environ["LLM_API_KEY"], timeout=25.0, max_retries=1)

    def ask(self, question: str) -> str:
        """A short answer to `question` from the web."""
        tool = {"type": "web_search_20250305", "name": "web_search", "max_uses": config.SEARCH_MAX_USES}
        system = time.strftime("It is now %A %d %B %Y, %H:%M local time. ") + _SYSTEM
        home = weather.home()
        if home is not None:  # local results ("weather", "open now") are about home
            tool["user_location"] = {"type": "approximate", "city": home.name, "region": home.region,
                                     "country": home.country_code, "timezone": home.timezone}
            system += f" The user lives in {home}."
        messages: list[dict] = [{"role": "user", "content": question}]
        searches = 0
        t0 = time.time()
        for _ in range(3):
            r = self.client.messages.create(model=config.MODEL, max_tokens=1024, system=system,
                                            messages=messages, tools=[tool])
            used = getattr(r.usage, "server_tool_use", None)
            searches += getattr(used, "web_search_requests", 0) or 0
            if r.stop_reason != "pause_turn":
                break
            # A long search turn paused: send it back unchanged to let it finish.
            messages = [messages[0], {"role": "assistant", "content": r.content}]
        text = " ".join(b.text.strip() for b in r.content if b.type == "text").strip()
        print(f"  (web search: {searches} search{'es' if searches != 1 else ''}, {time.time() - t0:.1f}s, "
              f"{r.usage.input_tokens} tokens in)")
        if r.stop_reason == "refusal" or not text:
            return "The search found nothing useful."
        return " ".join(text.split())  # citations split the text into pieces with stray spaces
