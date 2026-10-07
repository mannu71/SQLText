"""Stand-ins for the Bedrock client and chat models, so routing and RLM logic run without AWS."""
from types import SimpleNamespace

from sqltext.backends import ChatBackend


def text(t):
    return SimpleNamespace(type="text", text=t)


def tool(name, id, **input):
    return SimpleNamespace(type="tool_use", name=name, id=id, input=input)


def response(*content, stop_reason=None, model="fake"):
    if stop_reason is None:
        stop_reason = "tool_use" if any(b.type == "tool_use" for b in content) else "end_turn"
    return SimpleNamespace(content=list(content), stop_reason=stop_reason, model=model)


class FakeClient:
    """Mimics client.beta.messages.create; replies come from a script (responses or callables)."""

    def __init__(self, *script):
        self.script = list(script)
        self.calls = []
        self.beta = self
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(dict(kwargs, messages=list(kwargs.get("messages", []))))  # snapshot: the list grows
        reply = self.script.pop(0)
        return reply(kwargs) if callable(reply) else reply


class FakeChat(ChatBackend):
    def __init__(self, *replies, name="fake"):
        self.replies = list(replies)
        self.name = name
        self.prompts = []

    def chat(self, messages):
        self.prompts.append(messages)
        return self.replies.pop(0)
