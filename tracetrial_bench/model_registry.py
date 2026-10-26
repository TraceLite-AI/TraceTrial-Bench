from __future__ import annotations

import os
import re
import stat
import tomllib
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from types import MappingProxyType
from typing import Mapping
from urllib.parse import urlsplit


_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_SAFE_MODEL_IDENTIFIER = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]*(?:/[A-Za-z0-9][A-Za-z0-9._-]*)*$"
)
_SAFE_CURRENCY = re.compile(r"^[A-Z]{3}$")
_PRICING_KEYS = frozenset(
    {
        "currency",
        "uncached_input_per_million",
        "cache_read_input_per_million",
        "cache_write_input_per_million",
        "effective_at",
    }
)
SUPPORTED_CONTAINER_HARNESSES = frozenset(
    {"openclaw", "hermes", "codex", "claudecode", "deepseek"}
)
_SUPPORTED_API_MODES = frozenset({"chat_completions"})
INTERNAL_API_KEY_ENV = "HARNESSBENCH_MODEL_API_KEY"


@dataclass(frozen=True)
class ContainerBuildConfig:
    debian_mirror: str | None = None
    debian_security_mirror: str | None = None
    npm_registry: str | None = None

    @property
    def configured(self) -> bool:
        return self.debian_mirror is not None

    def hash_payload(self) -> dict[str, str]:
        if not self.configured:
            return {}
        return {
            "debian_mirror": self.debian_mirror or "",
            "debian_security_mirror": self.debian_security_mirror or "",
            "npm_registry": self.npm_registry or "",
        }


@dataclass(frozen=True)
class ModelPricing:
    currency: str
    uncached_input_per_million: Decimal
    cache_read_input_per_million: Decimal
    cache_write_input_per_million: Decimal
    effective_at: str | None = None

    def public_summary(self) -> dict[str, str | None]:
        return {
            "currency": self.currency,
            "uncached_input_per_million": _decimal_text(
                self.uncached_input_per_million
            ),
            "cache_read_input_per_million": _decimal_text(
                self.cache_read_input_per_million
            ),
            "cache_write_input_per_million": _decimal_text(
                self.cache_write_input_per_million
            ),
            "effective_at": self.effective_at,
        }


@dataclass(frozen=True)
class ModelProfile:
    alias: str
    provider: str
    model: str
    base_url: str
    api_mode: str
    openclaw_model: str
    hermes_provider: str
    hermes_model: str
    api_key: str = field(repr=False)
    deepseek_reasoning_effort: str = "max"
    litellm_disable_web_search: bool = False
    pricing: ModelPricing | None = None


@dataclass(frozen=True)
class ModelRuntimeSpec:
    alias: str
    provider: str
    model: str
    base_url: str
    api_mode: str
    harness: str
    harness_model: str
    harness_provider: str | None
    api_key: str = field(repr=False)
    deepseek_reasoning_effort: str = "max"
    litellm_disable_web_search: bool = False
    pricing: ModelPricing | None = None

    def public_summary(self) -> dict[str, object]:
        summary: dict[str, object] = {
            "alias": self.alias,
            "provider": self.provider,
            "model": self.model,
            "base_url": self.base_url,
            "api_mode": self.api_mode,
            "harness": self.harness,
            "harness_model": self.harness_model,
            "harness_provider": self.harness_provider,
        }
        if self.deepseek_reasoning_effort != "max":
            summary["deepseek_reasoning_effort"] = self.deepseek_reasoning_effort
        if self.litellm_disable_web_search:
            summary["litellm_disable_web_search"] = True
        if self.pricing is not None:
            summary["pricing"] = self.pricing.public_summary()
        return summary


class ModelRegistry:
    def __init__(
        self,
        profiles: Mapping[str, ModelProfile],
        *,
        source: Path,
        container_build: ContainerBuildConfig | None = None,
    ) -> None:
        self._profiles = MappingProxyType(dict(profiles))
        self.source = source
        self.container_build = container_build or ContainerBuildConfig()

    @classmethod
    def load(cls, path: Path) -> "ModelRegistry":
        source = path.expanduser()
        if source.is_symlink() or not source.is_file():
            raise ValueError(f"model registry is missing or symlinked: {source}")
        try:
            mode = stat.S_IMODE(source.stat().st_mode)
        except OSError as exc:
            raise ValueError(f"cannot inspect model registry: {source}") from exc
        if mode & 0o077:
            raise ValueError(
                "model registry permissions allow group or other access; "
                f"run: chmod 600 {source}"
            )
        try:
            payload = tomllib.loads(source.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
            raise ValueError(f"invalid model registry: {source}") from exc
        raw_models = payload.get("models") if isinstance(payload, dict) else None
        if not isinstance(raw_models, dict) or not raw_models:
            raise ValueError("model registry must contain a non-empty [models] table")
        profiles: dict[str, ModelProfile] = {}
        for alias, raw_profile in raw_models.items():
            if not isinstance(alias, str) or not alias or not isinstance(raw_profile, dict):
                raise ValueError("every model registry entry must be a named table")
            profiles[alias] = _parse_profile(alias, raw_profile)
        raw_container_build = payload.get("container_build")
        if raw_container_build is None:
            container_build = ContainerBuildConfig()
        elif not isinstance(raw_container_build, dict):
            raise ValueError("container_build must be a table")
        else:
            container_build = _parse_container_build(raw_container_build)
        return cls(
            profiles,
            source=source.resolve(),
            container_build=container_build,
        )

    def resolve(
        self,
        alias: str,
        harness: str,
    ) -> ModelRuntimeSpec:
        profile = self._profiles.get(alias)
        if profile is None:
            raise ValueError(f"model is not registered: {alias}")
        normalized_harness = harness.lower()
        if normalized_harness not in SUPPORTED_CONTAINER_HARNESSES:
            raise ValueError(f"container model registry does not support harness: {harness}")
        if normalized_harness == "openclaw":
            harness_model = profile.openclaw_model
            harness_provider = None
        elif normalized_harness == "hermes":
            harness_model = profile.hermes_model
            harness_provider = profile.hermes_provider
        else:
            harness_model = profile.model
            harness_provider = None
        return ModelRuntimeSpec(
            alias=alias,
            provider=profile.provider,
            model=profile.model,
            base_url=profile.base_url,
            api_mode=profile.api_mode,
            harness=normalized_harness,
            harness_model=harness_model,
            harness_provider=harness_provider,
            api_key=profile.api_key,
            deepseek_reasoning_effort=profile.deepseek_reasoning_effort,
            litellm_disable_web_search=profile.litellm_disable_web_search,
            pricing=profile.pricing,
        )

    @property
    def aliases(self) -> tuple[str, ...]:
        return tuple(sorted(self._profiles))


def default_model_registry_path() -> Path:
    override = os.environ.get("HARNESSBENCH_MODEL_REGISTRY")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".config" / "harnessbench" / "models.toml"


def _parse_profile(alias: str, raw: dict[str, object]) -> ModelProfile:
    if "api_key_env" in raw:
        raise ValueError(
            f"model {alias!r}: api_key_env is no longer supported; use api_key"
        )
    provider = _required_string(raw, "provider", alias)
    model = _required_string(raw, "model", alias)
    base_url = _required_string(raw, "base_url", alias).rstrip("/")
    api_mode = _required_string(raw, "api_mode", alias)
    api_key = _required_string(raw, "api_key", alias)
    if not _SAFE_IDENTIFIER.fullmatch(provider):
        raise ValueError(f"model {alias!r} has an unsafe provider id")
    if not _SAFE_MODEL_IDENTIFIER.fullmatch(model):
        raise ValueError(f"model {alias!r} has an unsafe model id")
    if not _is_valid_http_url(base_url):
        raise ValueError(f"model {alias!r} has an invalid base_url")
    if api_mode not in _SUPPORTED_API_MODES:
        raise ValueError(f"model {alias!r} has unsupported api_mode: {api_mode}")
    openclaw_model = _optional_string(
        raw, "openclaw_model", f"{provider}/{model}", alias
    )
    hermes_provider = _optional_string(
        raw, "hermes_provider", f"custom:{provider}", alias
    )
    hermes_model = _optional_string(raw, "hermes_model", model, alias)
    deepseek_reasoning_effort = _optional_string(
        raw, "deepseek_reasoning_effort", "max", alias
    )
    if not _SAFE_IDENTIFIER.fullmatch(deepseek_reasoning_effort):
        raise ValueError(
            f"model {alias!r} has an unsafe deepseek_reasoning_effort"
        )
    litellm_disable_web_search = _optional_bool(
        raw,
        "litellm_disable_web_search",
        False,
        alias,
    )
    pricing = _parse_model_pricing(alias, raw.get("pricing"))
    return ModelProfile(
        alias=alias,
        provider=provider,
        model=model,
        base_url=base_url,
        api_mode=api_mode,
        openclaw_model=openclaw_model,
        hermes_provider=hermes_provider,
        hermes_model=hermes_model,
        api_key=api_key,
        deepseek_reasoning_effort=deepseek_reasoning_effort,
        litellm_disable_web_search=litellm_disable_web_search,
        pricing=pricing,
    )


def _parse_model_pricing(alias: str, value: object) -> ModelPricing | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError(f"model {alias!r} pricing must be a table")
    unknown = sorted(set(value) - _PRICING_KEYS)
    if unknown:
        raise ValueError(
            f"model {alias!r} pricing has unsupported fields: {', '.join(unknown)}"
        )
    currency = _required_string(value, "currency", f"{alias}.pricing").upper()
    if not _SAFE_CURRENCY.fullmatch(currency):
        raise ValueError(f"model {alias!r} pricing has an invalid currency")
    uncached = _required_decimal_price(
        value,
        "uncached_input_per_million",
        alias,
    )
    cache_read = _required_decimal_price(
        value,
        "cache_read_input_per_million",
        alias,
    )
    cache_write = _optional_decimal_price(
        value,
        "cache_write_input_per_million",
        uncached,
        alias,
    )
    effective_at = _optional_nullable_string(value, "effective_at", alias)
    return ModelPricing(
        currency=currency,
        uncached_input_per_million=uncached,
        cache_read_input_per_million=cache_read,
        cache_write_input_per_million=cache_write,
        effective_at=effective_at,
    )


def _parse_container_build(raw: dict[str, object]) -> ContainerBuildConfig:
    values: dict[str, str] = {}
    for key in (
        "debian_mirror",
        "debian_security_mirror",
        "npm_registry",
    ):
        value = raw.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"container_build requires a non-empty {key}")
        normalized = value.strip().rstrip("/")
        if not _is_valid_http_url(normalized):
            raise ValueError(f"container_build has an invalid {key}")
        values[key] = normalized
    return ContainerBuildConfig(**values)


def _is_valid_http_url(value: str) -> bool:
    try:
        parsed_url = urlsplit(value)
        return bool(
            parsed_url.scheme in {"http", "https"}
            and parsed_url.hostname
            and parsed_url.username is None
            and parsed_url.password is None
            and not parsed_url.query
            and not parsed_url.fragment
        )
    except ValueError:
        return False


def _required_string(raw: dict[str, object], key: str, alias: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"model {alias!r} requires a non-empty {key}")
    return value.strip()


def _optional_string(
    raw: dict[str, object], key: str, default: str, alias: str
) -> str:
    value = raw.get(key, default)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"model {alias!r} has an invalid {key}")
    return value.strip()


def _optional_nullable_string(
    raw: dict[str, object], key: str, alias: str
) -> str | None:
    value = raw.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"model {alias!r} pricing has an invalid {key}")
    return value.strip()


def _optional_bool(
    raw: dict[str, object], key: str, default: bool, alias: str
) -> bool:
    value = raw.get(key, default)
    if not isinstance(value, bool):
        raise ValueError(f"model {alias!r} has an invalid {key}")
    return value


def _required_decimal_price(
    raw: dict[str, object], key: str, alias: str
) -> Decimal:
    if key not in raw:
        raise ValueError(f"model {alias!r} pricing requires {key}")
    return _decimal_price(raw[key], key, alias)


def _optional_decimal_price(
    raw: dict[str, object],
    key: str,
    default: Decimal,
    alias: str,
) -> Decimal:
    if key not in raw:
        return default
    return _decimal_price(raw[key], key, alias)


def _decimal_price(value: object, key: str, alias: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError(f"model {alias!r} pricing has an invalid {key}")
    text = str(value).strip()
    if not text:
        raise ValueError(f"model {alias!r} pricing has an invalid {key}")
    try:
        amount = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(
            f"model {alias!r} pricing has an invalid {key}"
        ) from exc
    if not amount.is_finite() or amount < 0:
        raise ValueError(f"model {alias!r} pricing has an invalid {key}")
    return amount


def _decimal_text(value: Decimal) -> str:
    if value == 0:
        return "0"
    return format(value.normalize(), "f")
