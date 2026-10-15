"""Provider-independent AI handoff contracts; consumer ChatGPT is attended only."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class AIProvider(Protocol):
    def prepare(self, package_path: Path) -> str: ...


@dataclass
class ManualChatGPTHandoff:
    """Expose a prepared package to the user without automating the ChatGPT website."""

    def prepare(self, package_path: Path) -> str:
        if not package_path.is_file():
            raise FileNotFoundError(package_path)
        return f"Manual ChatGPT Plus handoff ready: {package_path}"


class UnsupportedAIProvider(RuntimeError):
    pass


class OptionalAPIProvider:
    """Reserved interface for a separately configured supported AI API."""

    def prepare(self, package_path: Path) -> str:
        raise UnsupportedAIProvider(
            "No supported AI API provider is configured; use the attended handoff."
        )