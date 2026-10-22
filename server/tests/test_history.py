"""The conversation history stays valid when a reply is abandoned mid-tool
call. A tool result left without its call made the API refuse every later
request (400) until the brain restarted. No network: the model is faked."""

import threading
import unittest
from types import SimpleNamespace as NS

from brain import thinking


def chunk(content=None, tool=None):
    calls = None
    if tool is not None:
        calls = [NS(index=0, id=tool[0], function=NS(name=tool[1], arguments=tool[2]))]
    return NS(choices=[NS(delta=NS(content=content, tool_calls=calls))])


class FakeStream(list):
    def close(self):
        pass


class FakeClient:
    """Answers with a `look` call first, then with text."""

    def __init__(self):
        self.requests = []
        self.chat = NS(completions=NS(create=self.create))

    def create(self, messages, **kw):
        self.requests.append(messages)
        if not any(m.get("role") == "tool" for m in messages[-3:]):
            return FakeStream([chunk(tool=("call_1", "look", '{"direction": "left"}'))])
        return FakeStream([chunk(content="[happy] I see tea. "), chunk(content="Green tea.")])


def brain_with(actions):
    b = thinking.RobotBrain.__new__(thinking.RobotBrain)
    b.client = FakeClient()
    b.history = []
    b.actions = actions
    b.situation = None
    b.emotion = "neutral"
    b._inflight = None
    return b


class HistoryTests(unittest.TestCase):
    def test_a_finished_turn_is_kept_whole(self):
        b = brain_with({"look": lambda args: ("looked left", None)})
        said = list(b.reply("Which tea is this?"))
        self.assertEqual(said, ["I see tea.", "Green tea."])
        roles = [m["role"] for m in b.history]
        self.assertEqual(roles, ["user", "assistant", "tool", "assistant"])

    def test_abandoned_during_a_tool_call_leaves_nothing(self):
        # The human keeps talking while `look` is still moving the head:
        # main.py abandons the reply, then the action finishes anyway.
        started, release = threading.Event(), threading.Event()

        def slow_look(args):
            started.set()
            release.wait(2)
            return ("looked left", None)

        b = brain_with({"look": slow_look})
        out = []
        t = threading.Thread(target=lambda: out.extend(b.reply("Which tea is this?")))
        t.start()
        started.wait(2)
        b.abandon()
        release.set()
        t.join(2)
        self.assertEqual(b.history, [], "nothing from the abandoned turn may stay")
        # And the next question goes out with a valid history.
        list(b.reply("Hello?"))
        self.assertEqual(b.history[0]["role"], "user")

    def test_repair_drops_broken_tool_exchanges(self):
        broken = [
            {"role": "user", "content": "hi"},
            {"role": "tool", "tool_call_id": "x", "content": "orphan"},  # its call was cut
            {"role": "assistant", "content": "", "tool_calls": [{"id": "y", "type": "function",
                                                                  "function": {"name": "look", "arguments": "{}"}}]},
            {"role": "user", "content": "no result for y"},
            {"role": "assistant", "content": "ok", "tool_calls": [{"id": "z", "type": "function",
                                                                    "function": {"name": "look", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "z", "content": "done"},
        ]
        fixed = thinking._repair(broken)
        self.assertEqual([m["role"] for m in fixed], ["user", "user", "assistant", "tool"])
        self.assertEqual(fixed[-1]["tool_call_id"], "z")


if __name__ == "__main__":
    unittest.main()
