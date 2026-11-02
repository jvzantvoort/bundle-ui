from django import template
from django.utils import timezone

from ..services import parse_datetime

register = template.Library()


@register.filter
def isotime(value):
    """Render an API timestamp in the configured time zone."""
    parsed = value if hasattr(value, "tzinfo") else parse_datetime(value)
    if parsed is None:
        return "—"
    if timezone.is_aware(parsed):
        parsed = timezone.localtime(parsed)
    return parsed.strftime("%Y-%m-%d %H:%M")


@register.filter
def shortsum(value):
    return (value or "")[:12]
