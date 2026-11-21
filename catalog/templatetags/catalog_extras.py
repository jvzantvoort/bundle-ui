from django import template
from django.utils import timezone

from .. import images
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


@register.filter
def is_image(path):
    """True for files the UI shows as images."""
    return images.is_image(path or "")


@register.filter
def basename(path):
    return (path or "").rsplit("/", 1)[-1]
