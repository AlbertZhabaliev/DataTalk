from __future__ import annotations

import shutil
import subprocess


def ollama_available() -> bool:
    return shutil.which("ollama") is not None


def pull_model(model: str, timeout: int = 600) -> str:
    """Download a model via the local `ollama` CLI. Cross-platform."""
    if not ollama_available():
        raise RuntimeError(
            "ollama CLI not found. Install from https://ollama.com "
            "(works on macOS, Windows and Linux)."
        )
    proc = subprocess.run(
        ["ollama", "pull", model],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr or "ollama pull failed")
    return proc.stdout.strip()
