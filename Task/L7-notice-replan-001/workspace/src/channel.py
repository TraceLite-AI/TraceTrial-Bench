from .errors import ChannelRefused


def send(notice_id: str, size_bytes: int) -> dict:
    """Run the operation; the baseline reports failures with exceptions."""
    if size_bytes <= 0:
        raise ChannelRefused("size must be positive")
    if notice_id.startswith("REFUSE-"):
        raise ChannelRefused(f"refused for {notice_id}")
    return {"notice_id": notice_id, "sent": size_bytes}
