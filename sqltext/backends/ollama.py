"""Models served by a local Ollama instance (https://ollama.com)."""
from __future__ import annotations

import json
import urllib.request

from . import ChatBackend


class OllamaBackend(ChatBackend):
    name = "ollama"

    def __init__(self, model: str = "qwen2.5-coder:1.5b", host: str = "http://localhost:11434"):
        self.model = model
        self.host = host.rstrip("/")

    def chat(self, messages):
        body = json.dumps({
            "model": self.model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": 0},
        }).encode()
        req = urllib.request.Request(f"{self.host}/api/chat", body, {"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=300) as resp:
            return json.loads(resp.read())["message"]["content"]
