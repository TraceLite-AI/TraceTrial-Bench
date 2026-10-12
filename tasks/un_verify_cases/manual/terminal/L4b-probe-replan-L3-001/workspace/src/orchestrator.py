from .mountpt import verify
from .service import poll
from .endpoint import resolve


def run_probe(probe_id: str, device: str, min_free_kib: int, timeout_ms: int, host: str) -> dict:
    """Run the request; the baseline converts caught operation exceptions."""
    try:
        mounted = verify(device, min_free_kib)
        polled = poll(probe_id, timeout_ms)
        route = resolve(host)
    except Exception as exc:
        return {"ok": False, "code": getattr(exc, "code", "E-000"), "msg": str(exc)}
    return {"ok": True, "mountpt": mounted, "service": polled, "endpoint": route}
