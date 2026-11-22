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


@register.filter
def takentime(value):
    """A capture time as EXIF ("2026:11:01 12:00:00") or ISO 8601 gives it."""
    if not value:
        return "—"
    text = str(value).strip()
    if len(text) >= 19 and text[4] == ":" and text[7] == ":":
        return f"{text[:4]}-{text[5:7]}-{text[8:10]} {text[11:16]}"
    return isotime(text) if parse_datetime(text) else text


@register.filter
def resolution(value):
    if not value or not value.get("width") or not value.get("height"):
        return "—"
    return f"{value['width']} × {value['height']}"


@register.filter
def device_name(value):
    """Make and model, without repeating a make the model already starts with."""
    if not value:
        return ""
    make, model = (value.get("make") or "").strip(), (value.get("model") or "").strip()
    if make and model.lower().startswith(make.lower()):
        return model
    return " ".join(part for part in (make, model) if part)


@register.filter
def duration(seconds):
    if seconds in (None, ""):
        return ""
    total = int(round(float(seconds)))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02}:{secs:02}" if hours else f"{minutes}:{secs:02}"


@register.filter
def lookup(mapping, key):
    return (mapping or {}).get(key)


@register.filter
def exif_value(value):
    """An EXIF value as text. A missing value (null: an unknown 0/0 rational) is a dash;
    text ends at its first NUL, as cameras pad values with NULs and leftover bytes."""
    if value is None:
        return "—"
    if isinstance(value, list):
        return ", ".join(exif_value(v) for v in value)
    if isinstance(value, float):
        return f"{value:g}"
    return str(value).split("\x00", 1)[0].strip()
