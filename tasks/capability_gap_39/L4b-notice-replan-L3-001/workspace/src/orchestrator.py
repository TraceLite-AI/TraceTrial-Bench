from .render import render
from .channel import send
from .recipient import resolve


def dispatch_notice(notice_id: str, template_id: str, count: int, size_bytes: int, addr: str) -> dict:
    """Run the request; the baseline converts caught operation exceptions."""
    try:
        rendered = render(template_id, count)
        sent = send(notice_id, size_bytes)
        route = resolve(addr)
    except Exception as exc:
        return {"ok": False, "code": getattr(exc, "code", "E-000"), "msg": str(exc)}
    return {"ok": True, "render": rendered, "channel": sent, "recipient": route}
