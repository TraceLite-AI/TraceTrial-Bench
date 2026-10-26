from .errors import TemplateMissing


def render(template_id: str, count: int) -> dict:
    """Run the operation; the baseline reports failures with exceptions."""
    if count <= 0:
        raise TemplateMissing("count must be positive")
    if template_id.startswith("MISSING-"):
        raise TemplateMissing(f"no template for {template_id}")
    return {"template_id": template_id, "count": count, "rendered": True}
