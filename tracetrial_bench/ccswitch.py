from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SECRET_KEYS = {
    "apikey",
    "api_key",
    "token",
    "auth_token",
    "authorization",
    "password",
    "secret",
}


@dataclass(frozen=True)
class CCSwitchProvider:
    app_type: str
    provider_id: str
    name: str
    settings: dict[str, Any]
    meta: dict[str, Any]
    website_url: str | None = None
    category: str | None = None
    provider_type: str | None = None


@dataclass(frozen=True)
class HarnessProviderContext:
    source: str
    app_type: str
    provider_id: str
    provider_name: str
    env: dict[str, str]
    artifacts: dict[str, str]
    summary: dict[str, Any]


class CCSwitchError(ValueError):
    pass


def resolve_provider(
    *,
    harness: str,
    model: str,
    run_dir: Path,
    provider_id: str | None = None,
    provider_name: str | None = None,
    ccswitch_home: Path | None = None,
) -> HarnessProviderContext:
    root = ccswitch_home or Path.home() / ".cc-switch"
    app_types = _candidate_app_types(harness)
    provider = _select_provider(
        root=root,
        app_types=app_types,
        provider_id=provider_id,
        provider_name=provider_name,
    )
    if harness == "opencode":
        return _materialize_opencode(provider=provider, model=model, run_dir=run_dir)
    if harness == "claudecode":
        return _materialize_claudecode(provider=provider, model=model, run_dir=run_dir)
    if harness == "codex":
        return _materialize_codex(
            provider=provider, model=model, run_dir=run_dir, root=root
        )
    if harness == "openclaw":
        return _materialize_openclaw(provider=provider, model=model, run_dir=run_dir)
    raise CCSwitchError(
        f"ccswitch provider source is not supported for harness {harness!r}"
    )


def _candidate_app_types(harness: str) -> tuple[str, ...]:
    if harness == "opencode":
        return ("opencode", "claude")
    if harness == "claudecode":
        return ("claude",)
    if harness == "codex":
        return ("codex", "claude")
    if harness == "openclaw":
        return ("openclaw", "claude", "opencode")
    return (harness,)


def _select_provider(
    *,
    root: Path,
    app_types: tuple[str, ...],
    provider_id: str | None,
    provider_name: str | None,
) -> CCSwitchProvider:
    db_path = root / "cc-switch.db"
    if not db_path.exists():
        raise CCSwitchError(f"CC Switch database not found: {db_path}")

    rows: list[sqlite3.Row] = []
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        for app_type in app_types:
            query = (
                "select app_type, id, name, settings_config, meta, website_url, "
                "category, provider_type, is_current from providers where app_type = ?"
            )
            params: list[Any] = [app_type]
            if provider_id:
                query += " and id = ?"
                params.append(provider_id)
            elif provider_name:
                query += " and lower(name) = lower(?)"
                params.append(provider_name)
            query += " order by is_current desc, sort_index asc, name asc"
            rows.extend(conn.execute(query, params).fetchall())

    if not rows:
        selector = provider_id or provider_name or "current provider"
        raise CCSwitchError(f"no CC Switch provider found for {selector!r}")

    row = rows[0]
    return CCSwitchProvider(
        app_type=row["app_type"],
        provider_id=row["id"],
        name=row["name"],
        settings=_loads_object(row["settings_config"]),
        meta=_loads_object(row["meta"]),
        website_url=row["website_url"],
        category=row["category"],
        provider_type=row["provider_type"],
    )


def _materialize_opencode(
    *,
    provider: CCSwitchProvider,
    model: str,
    run_dir: Path,
) -> HarnessProviderContext:
    opencode_provider_id, model_id = _split_opencode_model(model)
    settings = _opencode_provider_config(provider=provider, model_id=model_id)
    config_root = run_dir / ".provider" / "opencode-config"
    data_root = run_dir / ".provider" / "opencode-data"
    state_root = run_dir / ".provider" / "opencode-state"
    config_dir = config_root / "opencode"
    config_dir.mkdir(parents=True, exist_ok=True)
    data_root.mkdir(parents=True, exist_ok=True)
    state_root.mkdir(parents=True, exist_ok=True)

    config_path = config_dir / "opencode.json"
    config = {
        "$schema": "https://opencode.ai/config.json",
        "provider": {
            opencode_provider_id: settings,
        },
    }
    config_path.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n")

    summary = {
        "source": "ccswitch",
        "source_app_type": provider.app_type,
        "provider_id": provider.provider_id,
        "provider_name": provider.name,
        "opencode_provider_id": opencode_provider_id,
        "model": model_id,
        "config_path": str(config_path),
        "settings": _redact(settings),
    }
    summary_path = run_dir / ".provider" / "ccswitch.summary.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n")

    return HarnessProviderContext(
        source="ccswitch",
        app_type=provider.app_type,
        provider_id=provider.provider_id,
        provider_name=provider.name,
        env={
            "XDG_CONFIG_HOME": str(config_root),
            "XDG_DATA_HOME": str(data_root),
            "XDG_STATE_HOME": str(state_root),
        },
        artifacts={
            "provider_summary": str(summary_path.relative_to(run_dir)),
            "opencode_config": str(config_path.relative_to(run_dir)),
        },
        summary=summary,
    )


def _materialize_claudecode(
    *,
    provider: CCSwitchProvider,
    model: str,
    run_dir: Path,
) -> HarnessProviderContext:
    env = _claude_env(provider=provider, model=model)
    summary = {
        "source": "ccswitch",
        "source_app_type": provider.app_type,
        "provider_id": provider.provider_id,
        "provider_name": provider.name,
        "model": model,
        "env": _redact(env),
    }
    summary_path = _write_provider_summary(run_dir, summary)
    return HarnessProviderContext(
        source="ccswitch",
        app_type=provider.app_type,
        provider_id=provider.provider_id,
        provider_name=provider.name,
        env=env,
        artifacts={"provider_summary": str(summary_path.relative_to(run_dir))},
        summary=summary,
    )


def _materialize_codex(
    *,
    provider: CCSwitchProvider,
    model: str,
    run_dir: Path,
    root: Path,
) -> HarnessProviderContext:
    provider_key = _sanitize_provider_id(provider.name.lower())
    codex_home = run_dir / ".provider" / "codex-home"
    codex_home.mkdir(parents=True, exist_ok=True)
    proxy_base_url = _ccswitch_proxy_codex_base_url(root)

    if provider.app_type == "codex":
        auth, config_text = _codex_config_from_codex_provider(
            provider=provider, model=model
        )
    else:
        auth, config_text = _codex_config_from_claude_provider(
            provider=provider,
            model=model,
            provider_key=provider_key,
            proxy_base_url=proxy_base_url,
        )

    auth_path = codex_home / "auth.json"
    config_path = codex_home / "config.toml"
    auth_path.write_text(json.dumps(auth, indent=2, ensure_ascii=False) + "\n")
    config_path.write_text(config_text)

    summary = {
        "source": "ccswitch",
        "source_app_type": provider.app_type,
        "provider_id": provider.provider_id,
        "provider_name": provider.name,
        "model": model,
        "codex_home": str(codex_home),
        "mode": "ccswitch_proxy" if proxy_base_url else "direct_responses",
        "proxy_base_url": proxy_base_url,
        "config_path": str(config_path),
        "auth_path": str(auth_path),
        "auth": _redact(auth),
        "config": config_text,
    }
    summary_path = _write_provider_summary(run_dir, summary)
    return HarnessProviderContext(
        source="ccswitch",
        app_type=provider.app_type,
        provider_id=provider.provider_id,
        provider_name=provider.name,
        env={"CODEX_HOME": str(codex_home)},
        artifacts={
            "provider_summary": str(summary_path.relative_to(run_dir)),
            "codex_config": str(config_path.relative_to(run_dir)),
        },
        summary=summary,
    )


def _materialize_openclaw(
    *,
    provider: CCSwitchProvider,
    model: str,
    run_dir: Path,
) -> HarnessProviderContext:
    provider_key, model_id = _split_provider_model(model)
    config = _openclaw_provider_config(provider=provider, model_id=model_id)
    config_root = run_dir / ".provider" / "openclaw-config"
    config_root.mkdir(parents=True, exist_ok=True)
    config_path = config_root / "openclaw.json"
    payload = {
        "models": {
            "providers": {
                provider_key: config,
            },
        },
        "agents": {
            "defaults": {
                "model": {
                    "primary": f"{provider_key}/{model_id}",
                },
                "models": {
                    f"{provider_key}/{model_id}": {
                        "alias": model_id,
                    },
                },
            },
        },
    }
    config_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")

    summary = {
        "source": "ccswitch",
        "source_app_type": provider.app_type,
        "provider_id": provider.provider_id,
        "provider_name": provider.name,
        "openclaw_provider_id": provider_key,
        "model": model_id,
        "config_path": str(config_path),
        "settings": _redact(config),
    }
    summary_path = _write_provider_summary(run_dir, summary)
    return HarnessProviderContext(
        source="ccswitch",
        app_type=provider.app_type,
        provider_id=provider.provider_id,
        provider_name=provider.name,
        env={
            # OpenClaw's core CLI resolves an explicit config from
            # OPENCLAW_CONFIG_PATH.  Keep the legacy name as a compatibility
            # alias for older integrations that still inspect it.
            "OPENCLAW_CONFIG_PATH": str(config_path),
            "OPENCLAW_CONFIG": str(config_path),
        },
        artifacts={
            "provider_summary": str(summary_path.relative_to(run_dir)),
            "openclaw_config": str(config_path.relative_to(run_dir)),
        },
        summary=summary,
    )


def _opencode_provider_config(
    *, provider: CCSwitchProvider, model_id: str
) -> dict[str, Any]:
    if provider.app_type == "opencode":
        config = dict(provider.settings)
        config.setdefault("npm", "@ai-sdk/openai-compatible")
        config.setdefault("models", {})
        if isinstance(config["models"], list):
            config["models"] = {str(item): {} for item in config["models"]}
        if isinstance(config["models"], dict):
            config["models"].setdefault(model_id, {})
        return config

    env = provider.settings.get("env")
    if not isinstance(env, dict):
        raise CCSwitchError(
            f"CC Switch provider {provider.name!r} does not have env settings"
        )

    api_key = _first_string(
        env, ("OPENAI_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY")
    )
    base_url = _first_string(env, ("OPENAI_BASE_URL", "BASE_URL", "ANTHROPIC_BASE_URL"))
    if not api_key:
        raise CCSwitchError(
            f"CC Switch provider {provider.name!r} does not contain an API key"
        )
    if not base_url:
        raise CCSwitchError(
            f"CC Switch provider {provider.name!r} does not contain a base URL"
        )

    models = _model_catalog_from_env(env)
    models.setdefault(model_id, {})
    return {
        "npm": "@ai-sdk/openai-compatible",
        "options": {
            "baseURL": _to_openai_compatible_base_url(base_url),
            "apiKey": api_key,
        },
        "models": models,
    }


def _claude_env(*, provider: CCSwitchProvider, model: str) -> dict[str, str]:
    if provider.app_type != "claude":
        raise CCSwitchError(f"provider {provider.name!r} is not a Claude Code provider")
    env = provider.settings.get("env")
    if not isinstance(env, dict):
        raise CCSwitchError(
            f"CC Switch provider {provider.name!r} does not have env settings"
        )

    resolved: dict[str, str] = {}
    for key, value in env.items():
        if isinstance(value, str) and value.strip():
            resolved[key] = value.strip()
    if "ANTHROPIC_AUTH_TOKEN" in resolved and "ANTHROPIC_API_KEY" not in resolved:
        resolved["ANTHROPIC_API_KEY"] = resolved["ANTHROPIC_AUTH_TOKEN"]
    if model:
        resolved["ANTHROPIC_MODEL"] = model
    if "ANTHROPIC_BASE_URL" not in resolved:
        raise CCSwitchError(
            f"CC Switch provider {provider.name!r} does not contain ANTHROPIC_BASE_URL"
        )
    if "ANTHROPIC_API_KEY" not in resolved and "ANTHROPIC_AUTH_TOKEN" not in resolved:
        raise CCSwitchError(
            f"CC Switch provider {provider.name!r} does not contain an Anthropic API token"
        )
    return resolved


def _codex_config_from_codex_provider(
    *,
    provider: CCSwitchProvider,
    model: str,
) -> tuple[dict[str, Any], str]:
    auth = provider.settings.get("auth")
    config = provider.settings.get("config")
    if not isinstance(auth, dict):
        auth = {}
    if not isinstance(config, str) or not config.strip():
        raise CCSwitchError(
            f"CC Switch Codex provider {provider.name!r} does not contain config TOML"
        )
    return auth, _ensure_codex_model(config, model)


def _codex_config_from_claude_provider(
    *,
    provider: CCSwitchProvider,
    model: str,
    provider_key: str,
    proxy_base_url: str | None,
) -> tuple[dict[str, Any], str]:
    env = provider.settings.get("env")
    if not isinstance(env, dict):
        raise CCSwitchError(
            f"CC Switch provider {provider.name!r} does not have env settings"
        )

    api_key = _first_string(
        env, ("OPENAI_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY")
    )
    base_url = _first_string(env, ("OPENAI_BASE_URL", "BASE_URL", "ANTHROPIC_BASE_URL"))
    if not api_key:
        raise CCSwitchError(
            f"CC Switch provider {provider.name!r} does not contain an API key"
        )
    if not base_url:
        raise CCSwitchError(
            f"CC Switch provider {provider.name!r} does not contain a base URL"
        )

    auth = (
        {"OPENAI_API_KEY": "PROXY_MANAGED"}
        if proxy_base_url
        else {"OPENAI_API_KEY": api_key}
    )
    base_url = proxy_base_url or _to_openai_compatible_base_url(base_url)
    config = (
        f'model_provider = "{provider_key}"\n'
        f'model = "{_toml_escape(model)}"\n'
        'model_reasoning_effort = "high"\n'
        "disable_response_storage = true\n\n"
        f"[model_providers.{provider_key}]\n"
        f'name = "{_toml_escape(provider.name)}"\n'
        f'base_url = "{_toml_escape(base_url)}"\n'
        'wire_api = "responses"\n'
        "requires_openai_auth = true\n"
    )
    return auth, config


def _openclaw_provider_config(
    *, provider: CCSwitchProvider, model_id: str
) -> dict[str, Any]:
    if provider.app_type == "openclaw":
        config = dict(provider.settings)
        models = config.setdefault("models", [])
        if isinstance(models, list) and not any(
            item.get("id") == model_id for item in models if isinstance(item, dict)
        ):
            models.append({"id": model_id, "name": model_id})
        return config

    api_key: str | None = None
    base_url: str | None = None
    if provider.app_type == "opencode":
        options = provider.settings.get("options")
        if isinstance(options, dict):
            api_key = _first_string(options, ("apiKey", "api_key", "OPENAI_API_KEY"))
            base_url = _first_string(
                options, ("baseURL", "base_url", "OPENAI_BASE_URL")
            )
    else:
        env = provider.settings.get("env")
        if isinstance(env, dict):
            api_key = _first_string(
                env, ("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY", "OPENAI_API_KEY")
            )
            base_url = _first_string(
                env, ("ANTHROPIC_BASE_URL", "OPENAI_BASE_URL", "BASE_URL")
            )

    if not api_key or not base_url:
        raise CCSwitchError(
            f"CC Switch provider {provider.name!r} cannot be converted to OpenClaw config"
        )

    return {
        "baseUrl": base_url.rstrip("/"),
        "apiKey": api_key,
        "api": (
            "anthropic-messages"
            if "/anthropic" in base_url.lower()
            else "openai-completions"
        ),
        "models": [{"id": model_id, "name": model_id}],
    }


def _split_opencode_model(model: str) -> tuple[str, str]:
    return _split_provider_model(model)


def _split_provider_model(model: str) -> tuple[str, str]:
    if "/" not in model:
        raise CCSwitchError("model must use provider/model form when using ccswitch")
    provider_id, model_id = model.split("/", 1)
    if not provider_id or not model_id:
        raise CCSwitchError("model must use provider/model form when using ccswitch")
    return _sanitize_provider_id(provider_id), model_id


def _model_catalog_from_env(env: dict[str, Any]) -> dict[str, dict[str, Any]]:
    model_keys = (
        "ANTHROPIC_MODEL",
        "ANTHROPIC_DEFAULT_SONNET_MODEL",
        "ANTHROPIC_DEFAULT_OPUS_MODEL",
        "ANTHROPIC_DEFAULT_HAIKU_MODEL",
        "ANTHROPIC_REASONING_MODEL",
        "OPENAI_MODEL",
    )
    models: dict[str, dict[str, Any]] = {}
    for key in model_keys:
        value = env.get(key)
        if isinstance(value, str) and value.strip():
            models[_strip_model_suffix(value.strip())] = {}
    return models


def _to_openai_compatible_base_url(base_url: str) -> str:
    trimmed = base_url.rstrip("/")
    lowered = trimmed.lower()
    for suffix in ("/api/anthropic", "/anthropic", "/claude"):
        if lowered.endswith(suffix):
            return f"{trimmed[: -len(suffix)]}/v1"
    if re.search(r"/v\d+$", trimmed):
        return trimmed
    return f"{trimmed}/v1"


def _first_string(values: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = values.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _strip_model_suffix(model: str) -> str:
    return re.sub(r"\[[^\]]+\]$", "", model).strip()


def _sanitize_provider_id(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_.-]+", "-", value).strip("-") or "ccswitch"


def _ensure_codex_model(config: str, model: str) -> str:
    if re.search(r"(?m)^\s*model\s*=", config):
        return re.sub(
            r'(?m)^(\s*model\s*=\s*)(["\']).*?\2',
            rf'\1"{_toml_escape(model)}"',
            config,
            count=1,
        )
    return f'model = "{_toml_escape(model)}"\n{config}'


def _toml_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _write_provider_summary(run_dir: Path, summary: dict[str, Any]) -> Path:
    summary_path = run_dir / ".provider" / "ccswitch.summary.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n")
    return summary_path


def _loads_object(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    value = json.loads(raw)
    return value if isinstance(value, dict) else {}


def _ccswitch_proxy_codex_base_url(root: Path) -> str | None:
    db_path = root / "cc-switch.db"
    if not db_path.exists():
        return None
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "select enabled, listen_address, listen_port from proxy_config where app_type = 'codex'"
        ).fetchone()
    if row is None or not row["enabled"]:
        return None
    port = int(row["listen_port"] or 0)
    if port <= 0:
        return None
    host = row["listen_address"] or "127.0.0.1"
    if host in {"0.0.0.0", "::"}:
        host = "127.0.0.1" if host == "0.0.0.0" else "::1"
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"http://{host}:{port}/v1"


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            lowered = key.lower()
            if any(secret in lowered for secret in SECRET_KEYS):
                redacted[key] = "<redacted>"
            else:
                redacted[key] = _redact(item)
        return redacted
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value
