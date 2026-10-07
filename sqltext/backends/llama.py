"""Local GGUF models through llama.cpp. CPU-only, memory-mapped, no GPU needed."""
from __future__ import annotations

import os
import sys

from ..hardware import MODELS, check_budget, default_threads
from . import ChatBackend


def resolve_model_path(model: str) -> str:
    """Accepts a size name (tiny), a local .gguf path, or 'hf-repo:file.gguf'."""
    if os.path.isfile(model):
        return model
    if model in MODELS:
        repo, filename = MODELS[model]["repo"], MODELS[model]["file"]
    elif ":" in model:
        repo, filename = model.split(":", 1)
    else:
        raise ValueError(
            f"model {model!r} is not a file, a size ({', '.join(MODELS)}) or 'hf-repo:file.gguf'"
        )
    try:
        from huggingface_hub import hf_hub_download, try_to_load_from_cache
    except ImportError as e:
        raise RuntimeError("install the llama extra: pip install 'sqltext[llama]'") from e
    cached = try_to_load_from_cache(repo, filename)
    if isinstance(cached, str):
        return cached  # works offline after the first download
    print(f"Downloading {repo}/{filename} (one time)...", file=sys.stderr)
    return hf_hub_download(repo, filename)


class LlamaBackend(ChatBackend):
    name = "llama"

    def __init__(self, model: str = "tiny", n_ctx: int = 4096, threads: int = None):
        try:
            from llama_cpp import Llama
        except ImportError as e:
            raise RuntimeError(
                "llama-cpp-python is not installed. Prebuilt CPU wheels:\n"
                "  pip install llama-cpp-python --extra-index-url "
                "https://abetlen.github.io/llama-cpp-python/whl/cpu"
            ) from e
        self.model_path = resolve_model_path(model)
        check_budget(self.model_path)
        self.llm = Llama(
            model_path=self.model_path,
            n_ctx=n_ctx,
            n_threads=threads or default_threads(),
            n_gpu_layers=0,
            use_mmap=True,
            verbose=False,
        )

    def chat(self, messages):
        out = self.llm.create_chat_completion(messages=messages, temperature=0.0, max_tokens=512)
        return out["choices"][0]["message"]["content"]
