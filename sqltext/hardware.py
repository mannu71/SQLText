"""Local model sizes and the local memory budget."""
from __future__ import annotations

import os

import psutil

# Local models must stay under this much RAM (override with SQLTEXT_LOCAL_RAM_GB).
# Measured: Needle + the tiny model together peak at ~0.8 GB including Python and drivers.
LOCAL_RAM_BUDGET_GB = float(os.environ.get("SQLTEXT_LOCAL_RAM_GB", "1.0"))

# GGUF models (Q4_K_M quantisation). RAM figure is measured peak process memory with a 4k context.
MODELS = {
    "tiny": {
        "repo": "Qwen/Qwen2.5-Coder-0.5B-Instruct-GGUF",
        "file": "qwen2.5-coder-0.5b-instruct-q4_k_m.gguf",
        "ram_gb": 0.8,
    },
}


def estimated_ram_gb(gguf_path: str) -> float:
    """Rough peak RAM for a GGUF model: weights + KV cache/buffers + the Python process."""
    return os.path.getsize(gguf_path) / 1024**3 * 1.25 + 0.3


def check_budget(gguf_path: str) -> None:
    need = estimated_ram_gb(gguf_path)
    if need > LOCAL_RAM_BUDGET_GB:
        raise ValueError(
            f"{os.path.basename(gguf_path)} needs ~{need:.1f} GB RAM, over the {LOCAL_RAM_BUDGET_GB:g} GB "
            "local budget. Use a smaller model, or raise SQLTEXT_LOCAL_RAM_GB."
        )


def default_threads() -> int:
    """Physical cores (hyper-threads slow llama.cpp down), leaving one free on bigger machines."""
    cores = psutil.cpu_count(logical=False) or os.cpu_count() or 1
    return max(1, cores - 1) if cores > 4 else cores
