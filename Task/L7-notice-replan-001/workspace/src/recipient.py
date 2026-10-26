from .errors import RecipientInvalid


def resolve(addr: str) -> dict:
    """Resolve the target; the baseline reports failures with exceptions."""
    if not addr or len(addr) < 5:
        raise RecipientInvalid("addr too short")
    return {"addr": addr, "hops": 3}
