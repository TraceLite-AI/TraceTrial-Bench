from .errors import EndpointInvalid


def resolve(host: str) -> dict:
    """Resolve the target; the baseline reports failures with exceptions."""
    if not host or len(host) < 5:
        raise EndpointInvalid("host too short")
    return {"host": host, "hops": 3}
