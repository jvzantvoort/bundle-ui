"""Image files in bundles: which files the UI shows as images, and the list per bundle."""
import posixpath

from django.core.cache import cache

# Types browsers display. MPO (multi-picture stereo photos from cameras) is JPEG
# based; browsers show its first picture. SVG is left out: it can carry scripts.
CONTENT_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".jpe": "image/jpeg",
    ".mpo": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".avif": "image/avif",
    ".bmp": "image/bmp",
}

# A bundle's files never change for a given checksum, so the list can be kept a
# long time; the cache only bounds memory.
CACHE_SECONDS = 6 * 60 * 60


def content_type(path):
    """The image type of a file, or None if the UI does not show it as an image."""
    return CONTENT_TYPES.get(posixpath.splitext(path)[1].lower())


def is_image(path):
    return content_type(path) is not None


def image_paths(server, pool, checksum, client=None):
    """Paths of the images in a bundle, in manifest order. Raises ApiError."""
    key = f"images:{server.pk}:{server.base_url}:{pool}:{checksum}"
    paths = cache.get(key)
    if paths is None:
        client = client or server.client()
        paths = [item["path"] for item in client.list_all_files(pool, checksum) if is_image(item["path"])]
        cache.set(key, paths, CACHE_SECONDS)
    return paths
