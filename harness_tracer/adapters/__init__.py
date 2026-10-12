from __future__ import annotations

from harness_tracer.adapters.base import AdapterResult, HarnessAdapter
from harness_tracer.adapters.claudecode import ClaudeCodeAdapter
from harness_tracer.adapters.codex import CodexAdapter
from harness_tracer.adapters.deepseek import DeepSeekAdapter
from harness_tracer.adapters.generic import GenericProcessAdapter
from harness_tracer.adapters.hermes import HermesAdapter
from harness_tracer.adapters.openclaw import OpenClawAdapter


def adapter_for(name: str) -> HarnessAdapter:
    normalized = name.lower()
    if normalized == "codex":
        return CodexAdapter()
    if normalized == "openclaw":
        return OpenClawAdapter()
    if normalized in {"deepseek", "deepseek-harness", "dsh"}:
        return DeepSeekAdapter()
    if normalized in {"hermes", "hermes-agent"}:
        return HermesAdapter()
    if normalized in {"claude", "claudecode"}:
        return ClaudeCodeAdapter(normalized)
    if normalized in {"fake", "opencode", "nanobot"}:
        return GenericProcessAdapter(normalized)
    raise ValueError(f"unknown harness: {name}")


__all__ = [
    "AdapterResult",
    "ClaudeCodeAdapter",
    "CodexAdapter",
    "DeepSeekAdapter",
    "GenericProcessAdapter",
    "HermesAdapter",
    "HarnessAdapter",
    "OpenClawAdapter",
    "adapter_for",
]
