"""Model backends. Each turns (schema, question) into a SQL string."""
from __future__ import annotations

from ..prompt import build_messages
from ..safety import extract_sql


class Backend:
    name = "base"

    def generate_sql(self, schema, question: str, error: str = None, previous_sql: str = None) -> str:
        raise NotImplementedError


class ChatBackend(Backend):
    """A backend driven by a chat-style LLM: build a prompt, extract the SQL from the reply."""

    def chat(self, messages: list) -> str:
        raise NotImplementedError

    def generate_sql(self, schema, question, error=None, previous_sql=None):
        return extract_sql(self.chat(build_messages(schema, question, error, previous_sql)))


def get_backend(name: str, model: str = None, **options) -> Backend:
    if name == "llama":
        from .llama import LlamaBackend
        return LlamaBackend(model or "auto", **options)
    if name == "ollama":
        from .ollama import OllamaBackend
        return OllamaBackend(model or "qwen2.5-coder:1.5b", **options)
    if name == "needle":
        from .needle import NeedleBackend
        return NeedleBackend(weights=model or None)
    raise ValueError(f"unknown backend {name!r} (choose llama, ollama or needle)")
