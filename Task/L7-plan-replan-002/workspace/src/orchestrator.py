from .stock import reserve
from .payment import charge
from .shipping import quote


def place_order(order_id: str, sku: str, qty: int, amount_minor: int, addr: str) -> dict:
    """Dispatch touring-stage equipment through the retained place_order API."""
    try:
        reserved = reserve(sku, qty)
        charged = charge(order_id, amount_minor)
        shipping = quote(addr)
    except Exception as exc:
        return {"ok": False, "code": getattr(exc, "code", "E-000"), "msg": str(exc)}
    return {"ok": True, "reserve": reserved, "charge": charged, "quote": shipping}
