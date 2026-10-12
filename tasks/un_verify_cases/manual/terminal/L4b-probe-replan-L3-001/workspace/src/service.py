from .errors import ServiceUnreachable


def poll(probe_id: str, timeout_ms: int) -> dict:
    """Run the operation; the baseline reports failures with exceptions."""
    if timeout_ms <= 0:
        raise ServiceUnreachable("timeout must be positive")
    if probe_id.startswith("UNREACH-"):
        raise ServiceUnreachable(f"unreachable for {probe_id}")
    return {"probe_id": probe_id, "polled": timeout_ms}
