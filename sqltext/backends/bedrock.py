"""Claude on Amazon Bedrock, through the Anthropic SDK's Bedrock (Mantle) client.

Credentials come from the usual AWS chain (env vars, ~/.aws, instance role).
"""
from __future__ import annotations

import os

from . import ChatBackend

STRONG_MODEL = "anthropic.claude-opus-5-5"   # RLM root: plans, explores the schema, writes hard SQL
FAST_MODEL = "anthropic.claude-haiku-4-5"    # escalations, recursive sub-calls, query rewrites
# Opus 5.5 runs safety classifiers that can occasionally decline a benign request; Bedrock has no
# server-side fallback, so the SDK middleware retries a refusal on this model instead.
REFUSAL_FALLBACK = "anthropic.claude-opus-5"


def make_client(region: str = None):
    try:
        from anthropic import AnthropicBedrockMantle, BetaRefusalFallbackMiddleware
    except ImportError as e:
        raise RuntimeError("install the bedrock extra: pip install 'sqltext[bedrock]'") from e
    region = region or os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"
    return AnthropicBedrockMantle(
        aws_region=region,
        middleware=[BetaRefusalFallbackMiddleware([{"model": REFUSAL_FALLBACK}])],
    )


def model_params(model: str, effort: str = "medium") -> dict:
    """Per-model request settings. Haiku 4.5 does not take `effort`; Opus 5.5 always thinks
    (adaptive) and its effort defaults to medium, so it is set explicitly."""
    if "haiku" in model:
        return {}
    return {"output_config": {"effort": effort}}


def text_of(response) -> str:
    if response.stop_reason == "refusal":
        raise RuntimeError(f"{response.model} declined the request")
    return "".join(b.text for b in response.content if b.type == "text")


class BedrockBackend(ChatBackend):
    name = "bedrock"

    def __init__(self, model: str = FAST_MODEL, region: str = None, effort: str = "medium", client=None):
        self.model = model
        self.effort = effort
        self.client = client or make_client(region)

    def chat(self, messages):
        system = "\n".join(m["content"] for m in messages if m["role"] == "system")
        turns = [m for m in messages if m["role"] != "system"]
        response = self.client.beta.messages.create(
            model=self.model,
            max_tokens=16000,
            system=system,
            messages=turns,
            **model_params(self.model, self.effort),
        )
        return text_of(response)
