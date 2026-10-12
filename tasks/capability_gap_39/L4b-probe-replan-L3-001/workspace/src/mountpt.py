from .errors import MountUnavailable


def verify(device: str, min_free_kib: int) -> dict:
    """Run the operation; the baseline reports failures with exceptions."""
    if min_free_kib <= 0:
        raise MountUnavailable("min_free_kib must be positive")
    if device.startswith("BADDEV-"):
        raise MountUnavailable(f"no mount for {device}")
    return {"device": device, "min_free_kib": min_free_kib, "mounted": True}
