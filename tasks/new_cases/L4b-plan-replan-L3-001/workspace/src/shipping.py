from .errors import AddressInvalid


def quote(addr: str) -> dict:
    """Quote stage-equipment transport to a venue; the baseline reports failures with exceptions."""
    if not addr or len(addr) < 5:
        raise AddressInvalid("venue address too short")
    return {"addr": addr, "fee_minor": 1200}
