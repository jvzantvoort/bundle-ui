"""Media metadata of bundle files: EXIF data, resolution, capture time, GPS and device.

bundle-python stores it in a bundle's `.bundle/MEDIA.json`; bundle-server (API 1.2.0)
serves it read-only and links it from a bundle as `links.media`. Bundles that were never
scanned, and older servers, have no such link: the UI then shows no media information.
"""
import logging

from .client import ApiError

log = logging.getLogger(__name__)


def has_media(bundle_data):
    return bool((bundle_data or {}).get("links", {}).get("media"))


def _fetch(call, *args):
    """The answer, or None when there is no media metadata or it cannot be read."""
    try:
        return call(*args)
    except ApiError as exc:
        if exc.status != 404:
            log.warning("cannot read media metadata: %s", exc.message)
        return None


def bundle_media(client, pool, checksum, bundle_data):
    """The media index of a bundle, with `by_path` added, or None."""
    if not has_media(bundle_data):
        return None
    index = _fetch(client.get_media, pool, checksum)
    if index is None:
        return None
    index["by_path"] = {entry["path"]: entry for entry in index.get("files", [])}
    return index


def file_media(client, pool, checksum, file_path):
    """Media metadata of one file, with its EXIF tags sorted by name as `exif_items`, or None."""
    entry = _fetch(client.get_file_media, pool, checksum, file_path)
    if entry is not None:
        entry["exif_items"] = sorted((entry.get("exif") or {}).items(), key=lambda item: item[0].lower())
    return entry
