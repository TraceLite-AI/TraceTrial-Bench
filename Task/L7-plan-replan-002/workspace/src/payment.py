from .errors import PaymentDeclined


def charge(order_id: str, amount_minor: int) -> dict:
    """Authorize a production-budget charge; the baseline reports failures with exceptions."""
    if amount_minor <= 0:
        raise PaymentDeclined("production budget amount must be positive")
    if order_id.startswith("DECLINE-"):
        raise PaymentDeclined(f"production budget declined for {order_id}")
    return {"order_id": order_id, "charged": amount_minor}
