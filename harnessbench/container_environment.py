from __future__ import annotations

import os
from typing import Mapping


TRANSPORT_ENV_NAMES = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "no_proxy",
)
LOOPBACK_NO_PROXY = ("127.0.0.1", "localhost", "::1")


def resolve_transport_environment(
    environ: Mapping[str, str] | None = None,
) -> dict[str, str]:
    values = os.environ if environ is None else environ
    return {
        name: value
        for name in TRANSPORT_ENV_NAMES
        if isinstance((value := values.get(name)), str) and value
    }


def add_loopback_no_proxy(environment: Mapping[str, str]) -> dict[str, str]:
    resolved = dict(environment)
    for name in ("NO_PROXY", "no_proxy"):
        entries = [
            item.strip()
            for item in resolved.get(name, "").split(",")
            if item.strip()
        ]
        for loopback in LOOPBACK_NO_PROXY:
            if loopback not in entries:
                entries.append(loopback)
        resolved[name] = ",".join(entries)
    return resolved
