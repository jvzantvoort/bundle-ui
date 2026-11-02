"""Thin HTTP client for the Bundle Server API (see openapi.yaml)."""
import base64
import binascii
import json
from datetime import datetime, timezone
from urllib.parse import quote, urlsplit, urlunsplit

import requests
from django.conf import settings


class ApiError(Exception):
    """The server answered with an error, or could not be reached (status is None)."""

    def __init__(self, message, status=None, code=""):
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code

    @property
    def unreachable(self):
        return self.status is None


def normalize_base_url(url):
    """Reduce any API URL to its `<scheme>://<host>/.../api/v1` root.

    Accepts a bare host URL or a deep link such as `.../api/v1/pools/default`.
    """
    parts = urlsplit(url.strip())
    path = parts.path.rstrip("/")
    head, marker, _ = path.partition("/api/v1")
    path = f"{head}/api/v1" if marker else f"{path}/api/v1"
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


def token_expiry(token):
    """Return the `exp` of a JWT as an aware datetime, or None. The signature is not checked."""
    try:
        payload = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        return datetime.fromtimestamp(claims["exp"], tz=timezone.utc)
    except (IndexError, KeyError, TypeError, ValueError, OverflowError, binascii.Error):
        return None


class BundleClient:
    def __init__(self, base_url, token=""):
        self.base_url = base_url.rstrip("/")
        self.token = token

    def _request(self, method, path, *, params=None, json_body=None, if_match=None):
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if if_match:
            headers["If-Match"] = if_match
        try:
            response = requests.request(
                method,
                self.base_url + path,
                params=params,
                json=json_body,
                headers=headers,
                timeout=(settings.BUNDLE_API_CONNECT_TIMEOUT, settings.BUNDLE_API_READ_TIMEOUT),
            )
        except requests.RequestException as exc:
            raise ApiError(f"Cannot reach {self.base_url}: {type(exc).__name__}") from exc

        if response.status_code >= 400:
            try:
                body = response.json()
                message, code = body.get("message", ""), body.get("code", "")
            except (ValueError, AttributeError):
                message, code = response.text[:200].strip(), ""
            raise ApiError(message or f"HTTP {response.status_code}", response.status_code, code)
        try:
            return response.json(), response.headers.get("ETag")
        except ValueError as exc:
            raise ApiError(
                f"{self.base_url} returned a non-JSON response", response.status_code, "bad_response"
            ) from exc

    @staticmethod
    def _bundle_path(pool, checksum):
        return f"/pools/{quote(pool, safe='')}/bundles/{quote(checksum, safe='')}"

    def health(self):
        return self._request("GET", "/health")[0]

    def capabilities(self):
        return self._request("GET", "/capabilities")[0]

    def list_pools(self):
        return self._request("GET", "/pools")[0]["pools"]

    def get_pool(self, pool):
        return self._request("GET", f"/pools/{quote(pool, safe='')}")[0]

    def pool_stats(self, pool):
        return self._request("GET", f"/pools/{quote(pool, safe='')}/stats")[0]

    def list_bundles(self, pool, **filters):
        params = {key: value for key, value in filters.items() if value not in (None, "")}
        return self._request("GET", f"/pools/{quote(pool, safe='')}/bundles", params=params)[0]

    def query_bundles(self, pool, payload):
        path = f"/pools/{quote(pool, safe='')}/bundles/query"
        return self._request("POST", path, json_body=payload)[0]

    def get_bundle(self, pool, checksum):
        return self._request("GET", self._bundle_path(pool, checksum))

    def list_files(self, pool, checksum, page=1, page_size=50):
        path = self._bundle_path(pool, checksum) + "/files"
        return self._request("GET", path, params={"page": page, "page_size": page_size})[0]

    def update_title(self, pool, checksum, title, revision):
        return self._request(
            "PATCH", self._bundle_path(pool, checksum), json_body={"title": title}, if_match=revision
        )

    def add_tags(self, pool, checksum, tags, revision):
        path = self._bundle_path(pool, checksum) + "/tags"
        return self._request("POST", path, json_body={"tags": tags}, if_match=revision)

    def remove_tags(self, pool, checksum, tags, revision):
        path = self._bundle_path(pool, checksum) + "/tags"
        return self._request("DELETE", path, json_body={"tags": tags}, if_match=revision)

    def verify(self, pool, checksum):
        return self._request("POST", self._bundle_path(pool, checksum) + "/verify")[0]

    def task(self, task_id):
        return self._request("GET", f"/tasks/{quote(task_id, safe='')}")[0]
