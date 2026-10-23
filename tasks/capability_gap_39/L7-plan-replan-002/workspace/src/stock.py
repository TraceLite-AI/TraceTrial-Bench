from .errors import OutOfStock


def reserve(sku: str, qty: int) -> dict:
    """Reserve touring-stage equipment; the baseline reports failures with exceptions."""
    if qty <= 0:
        raise OutOfStock("qty must be positive")
    if sku.startswith("OOS-"):
        raise OutOfStock(f"no stock for {sku}")
    return {"sku": sku, "qty": qty, "reserved": True}
