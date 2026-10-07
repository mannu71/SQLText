"""Pick a model size and runtime settings that fit the machine."""
from __future__ import annotations

import os

import psutil

# GGUF models (Q4_K_M quantisation). RAM figures are measured peak process memory with a 4k context.
MODELS = {
    "tiny": {
        "repo": "Qwen/Qwen2.5-Coder-0.5B-Instruct-GGUF",
        "file": "qwen2.5-coder-0.5b-instruct-q4_k_m.gguf",
        "ram_gb": 0.8,
    },
    "small": {
        "repo": "Qwen/Qwen2.5-Coder-1.5B-Instruct-GGUF",
        "file": "qwen2.5-coder-1.5b-instruct-q4_k_m.gguf",
        "ram_gb": 2.0,
    },
    "medium": {
        "repo": "Qwen/Qwen2.5-Coder-3B-Instruct-GGUF",
        "file": "qwen2.5-coder-3b-instruct-q4_k_m.gguf",
        "ram_gb": 3.8,
    },
}


def available_ram_gb() -> float:
    return psutil.virtual_memory().available / 1024**3


def pick_model_size(ram_gb: float = None) -> str:
    """Largest model that leaves headroom for the OS and the database driver."""
    ram_gb = available_ram_gb() if ram_gb is None else ram_gb
    for size in ("medium", "small"):
        if ram_gb >= MODELS[size]["ram_gb"] * 1.5:
            return size
    return "tiny"


def default_threads() -> int:
    """Physical cores (hyper-threads slow llama.cpp down), leaving one free on bigger machines."""
    cores = psutil.cpu_count(logical=False) or os.cpu_count() or 1
    return max(1, cores - 1) if cores > 4 else cores
