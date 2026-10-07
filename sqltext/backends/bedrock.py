"""Claude on Amazon Bedrock, through the Anthropic SDK's Bedrock (Mantle) client.

Credentials come from the usual AWS chain (env vars, ~/.aws, instance role).
"""
from __future__ import annotations

import os
import time

from ..prompt import build_messages
from ..safety import extract_sql
from . import ChatBackend

STRONG_MODEL = "anthropic.claude-opus-5-5"   # RLM root: plans, explores the schema, writes hard SQL
FAST_MODEL = "anthropic.claude-haiku-4-5"    # escalations, recursive sub-calls, query rewrites
# Opus 5.5 runs safety classifiers that can occasionally decline a benign request; Bedrock has no
# server-side fallback, so the SDK middleware retries a refusal on this model instead.
REFUSAL_FALLBACK = "anthropic.claude-opus-5"


# USD per million tokens (input, output) at Anthropic list prices. Bedrock sets its own prices,
# so treat costs as estimates; override with SQLTEXT_PRICES="model=in/out,model=in/out".
PRICES = {
    "anthropic.claude-opus-5-5": (4.0, 20.0),
    "anthropic.claude-opus-5": (5.0, 25.0),
    "anthropic.claude-haiku-4-5": (1.0, 5.0),
}
for _item in filter(None, os.environ.get("SQLTEXT_PRICES", "").split(",")):
    _model, _, _pair = _item.partition("=")
    PRICES[_model.strip()] = tuple(float(x) for x in _pair.split("/"))

TIMEOUT_S = 120.0   # per request; the SDK default is 10 minutes
MAX_RETRIES = 3     # the SDK retries 429/5xx/connection errors with exponential backoff


class BudgetExceeded(RuntimeError):
    pass


class CircuitOpen(RuntimeError):
    pass


def _price_key(model: str) -> str:
    """Map 'us.anthropic.claude-haiku-4-5' or a dated variant onto a PRICES key."""
    return next((k for k in sorted(PRICES, key=len, reverse=True) if k in model), model)


class BedrockGate:
    """Every Bedrock call goes through here: a spending cap, a circuit breaker that stops calling
    after repeated outages, and token/cost accounting."""

    def __init__(self, client, max_cost: float = None, failure_threshold: int = 3, cooldown_s: float = 60.0):
        self.client = client
        self.max_cost = max_cost
        self.failure_threshold = failure_threshold
        self.cooldown_s = cooldown_s
        self.failures = 0
        self.open_until = 0.0
        self.calls = 0
        self.input_tokens = self.output_tokens = self.cache_read_tokens = self.cache_write_tokens = 0
        self.cost = 0.0

    def create(self, **kwargs):
        if self.max_cost is not None and self.cost >= self.max_cost:
            raise BudgetExceeded(f"Bedrock budget of ${self.max_cost:.2f} used up (spent ${self.cost:.4f})")
        if time.monotonic() < self.open_until:
            raise CircuitOpen(f"Bedrock skipped for {self.open_until - time.monotonic():.0f}s after "
                              f"{self.failure_threshold} failed calls in a row")
        try:
            response = self.client.beta.messages.create(**kwargs)
        except Exception as e:
            if _is_outage(e):
                self.failures += 1
                if self.failures >= self.failure_threshold:
                    self.open_until = time.monotonic() + self.cooldown_s
            raise
        self.failures = 0
        self._record(response)
        return response

    def _record(self, response):
        u = getattr(response, "usage", None)
        if u is None:
            return
        self.calls += 1
        read = getattr(u, "cache_read_input_tokens", 0) or 0
        write = getattr(u, "cache_creation_input_tokens", 0) or 0
        self.input_tokens += u.input_tokens or 0
        self.output_tokens += u.output_tokens or 0
        self.cache_read_tokens += read
        self.cache_write_tokens += write
        price = PRICES.get(_price_key(getattr(response, "model", "") or ""))
        if price:
            p_in, p_out = price
            # cache writes bill at 1.25x input, cache reads at 0.1x (5-minute cache)
            self.cost += ((u.input_tokens or 0) * p_in + write * p_in * 1.25 + read * p_in * 0.1
                          + (u.output_tokens or 0) * p_out) / 1e6

    def summary(self) -> str:
        return (f"bedrock: {self.calls} calls, {self.input_tokens} in / {self.output_tokens} out tokens, "
                f"{self.cache_read_tokens} cache-read, ~${self.cost:.4f}")


def _is_outage(e: Exception) -> bool:
    """Errors that mean Bedrock is unavailable (after the SDK's own retries), not that our request was bad."""
    try:
        import anthropic
    except ImportError:
        return False
    if isinstance(e, (anthropic.APIConnectionError, anthropic.RateLimitError)):
        return True
    return isinstance(e, anthropic.APIStatusError) and e.status_code >= 500


def make_client(region: str = None):
    try:
        from anthropic import AnthropicBedrockMantle, BetaRefusalFallbackMiddleware
    except ImportError as e:
        raise RuntimeError("install the bedrock extra: pip install 'sqltext[bedrock]'") from e
    region = region or os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"
    return AnthropicBedrockMantle(
        aws_region=region,
        timeout=TIMEOUT_S,
        max_retries=MAX_RETRIES,
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

    def __init__(self, model: str = FAST_MODEL, region: str = None, effort: str = "medium", client=None,
                 gate: BedrockGate = None, max_cost: float = None):
        self.model = model
        self.effort = effort
        self.gate = gate or BedrockGate(client or make_client(region), max_cost=max_cost)

    def generate_sql(self, schema, question, error=None, previous_sql=None):
        # The schema block comes first and is marked for prompt caching, so repeated questions about
        # the same database reuse it (Bedrock caches it once it reaches the model's minimum size).
        return extract_sql(self.chat(build_messages(schema, question, error, previous_sql, cache_schema=True)))

    def chat(self, messages):
        system = "\n".join(m["content"] for m in messages if m["role"] == "system")
        turns = [m for m in messages if m["role"] != "system"]
        response = self.gate.create(
            model=self.model,
            max_tokens=16000,
            system=[{"type": "text", "text": system}],
            messages=turns,
            **model_params(self.model, self.effort),
        )
        return text_of(response)
