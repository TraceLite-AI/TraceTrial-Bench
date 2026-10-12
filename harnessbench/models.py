from __future__ import annotations

import subprocess
from dataclasses import dataclass

from harnessbench.types import ModelBinding


@dataclass(frozen=True)
class BindingRule:
    mode: str
    flag: str | None
    value_style: str
    allowed_providers: set[str] | None = None
    validation: str = "not_validated"


DEFAULT_BINDINGS: dict[str, BindingRule] = {
    "codex": BindingRule(
        mode="cli_arg",
        flag="--model",
        value_style="canonical",
        allowed_providers={"openai", "codex"},
        validation="codex_catalog_optional",
    ),
    "claudecode": BindingRule(
        mode="cli_arg",
        flag="--model",
        value_style="canonical",
        allowed_providers={"anthropic", "claude"},
        validation="not_validated",
    ),
    "opencode": BindingRule(
        mode="cli_arg",
        flag="-m",
        value_style="requested",
        allowed_providers=None,
        validation="not_validated",
    ),
    "openclaw": BindingRule(
        mode="cli_arg",
        flag="--model",
        value_style="requested",
        allowed_providers=None,
        validation="not_validated",
    ),
    "hermes": BindingRule(
        mode="cli_arg",
        flag="--model",
        value_style="requested",
        allowed_providers=None,
        validation="not_validated",
    ),
    "deepseek": BindingRule(
        mode="sdk_handshake",
        flag=None,
        value_style="canonical",
        allowed_providers=None,
        validation="not_validated",
    ),
    "fake": BindingRule(
        mode="cli_arg",
        flag="--model",
        value_style="canonical",
        allowed_providers=None,
        validation="not_validated",
    ),
}


def bind_model(
    *,
    harness: str,
    model: str,
    harness_path: str | None = None,
    validate: bool = False,
) -> ModelBinding:
    normalized = normalize_harness_name(harness)
    provider, canonical = _split_model(model)
    rule = DEFAULT_BINDINGS.get(normalized)
    if rule is None:
        raise ValueError(f"unknown harness for model binding: {harness}")

    if (
        provider is not None
        and rule.allowed_providers
        and provider not in rule.allowed_providers
    ):
        allowed = ", ".join(sorted(rule.allowed_providers))
        raise ValueError(
            f"model {model!r} is not supported by harness {normalized!r}; "
            f"allowed providers: {allowed}"
        )

    harness_arg = _binding_value(rule, requested=model, canonical=canonical)
    validation_status, validation_message = _validate_binding(
        harness=normalized,
        harness_path=harness_path,
        harness_arg=harness_arg,
        rule=rule,
        enabled=validate,
    )
    binding_status = "explicit"
    if validation_status == "failed":
        binding_status = "failed"

    return ModelBinding(
        requested=model,
        provider=provider,
        canonical=canonical,
        harness_arg=harness_arg,
        binding_status=binding_status,
        binding_mode=rule.mode,
        flag=rule.flag,
        validation_status=validation_status,
        validation_message=validation_message,
        observed=None,
    )


def normalize_harness_name(harness: str) -> str:
    normalized = harness.lower()
    if normalized in {"claude", "cc", "claude-code"}:
        return "claudecode"
    if normalized == "hermes-agent":
        return "hermes"
    if normalized in {"deepseek-harness", "dsh"}:
        return "deepseek"
    return normalized


def _split_model(model: str) -> tuple[str | None, str]:
    if ":" not in model:
        return None, model
    provider, canonical = model.split(":", 1)
    return provider or None, canonical


def _binding_value(rule: BindingRule, *, requested: str, canonical: str) -> str:
    if rule.value_style == "requested":
        return requested
    return canonical


def _validate_binding(
    *,
    harness: str,
    harness_path: str | None,
    harness_arg: str,
    rule: BindingRule,
    enabled: bool,
) -> tuple[str, str | None]:
    if not enabled:
        return "not_validated", None
    if rule.validation != "codex_catalog_optional":
        return "not_validated", None
    if harness != "codex":
        return "not_validated", None

    executable = harness_path or "codex"
    try:
        completed = subprocess.run(
            [executable, "debug", "models"],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return "failed", str(exc)

    catalog_text = f"{completed.stdout}\n{completed.stderr}"
    if completed.returncode != 0:
        return (
            "failed",
            catalog_text.strip() or f"codex debug models exited {completed.returncode}",
        )
    if harness_arg in catalog_text:
        return "catalog_validated", None
    return "failed", f"model {harness_arg!r} not found in codex debug models output"
