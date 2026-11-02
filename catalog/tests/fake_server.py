"""In-memory stand-in for bundle-server, so writes can be tested without touching real data.

It enforces the parts of openapi.yaml the UI relies on: bearer auth, required If-Match
with 412 on a stale revision, and the live server's timestamp format. Run it directly
for manual testing: `python -m catalog.tests.fake_server 8099`.
"""
import json
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit

TOKEN = "test-token"


def sum_of(char):
    return char * 64


def make_bundle(char, title, tags=(), **extra):
    data = {
        "checksum": sum_of(char),
        "title": title,
        "author": "alice",
        "created_at": f"2026-11-0{(ord(char) % 8) + 1}T14:08:17.721919611+01:00",
        "version": 2,
        "file_count": 2,
        "size_bytes": 2048,
        "verified": False,
        "last_checked": None,
        "tags": list(tags),
        "rev": 1,
        "corrupt": False,
        "files": [
            {"path": f"photos/{char}-one.jpg", "checksum": sum_of("1"), "size_bytes": 1024},
            {"path": f"photos/{char}-two.jpg", "checksum": sum_of("2"), "size_bytes": 1024},
        ],
    }
    data.update(extra)
    return data


class FakeState:
    def __init__(self):
        self.reset()

    def reset(self):
        self.pools = {
            "default": {
                "title": "Default Pool",
                "bundles": {
                    sum_of("a"): make_bundle("a", "Iceland", ["travel", "nature"]),
                    sum_of("b"): make_bundle("b", "", ["travel"], author="bob"),
                    sum_of("c"): make_bundle("c", "Corrupt one", corrupt=True),
                },
            },
            "archive": {"title": "Archive", "bundles": {}},
        }
        self.tasks = {}
        self.healthy = True
        self.requests = []


def summary(bundle):
    keys = ("checksum", "title", "author", "created_at", "version", "file_count", "size_bytes", "verified")
    return {key: bundle[key] for key in keys}


def revision(bundle):
    return f'"rev{bundle["rev"]}"'


def detail(bundle):
    return {
        **summary(bundle),
        "metadata": {
            "title": bundle["title"], "created_at": bundle["created_at"],
            "author": bundle["author"], "version": bundle["version"],
        },
        "state": {
            "verified": bundle["verified"], "last_checked": bundle["last_checked"],
            "replicas": [], "size_bytes": bundle["size_bytes"],
        },
        "tags": sorted(bundle["tags"]),
        "revision": revision(bundle),
    }


def paged(items, page, page_size):
    start = (page - 1) * page_size
    return {"items": items[start:start + page_size], "page": page, "page_size": page_size, "total": len(items)}


def sort_key(sort):
    field = (sort or "title").lstrip("-")
    return (lambda b: b[field]), (sort or "").startswith("-")


class Handler(BaseHTTPRequestHandler):
    state = None

    def log_message(self, *args):
        pass

    def reply(self, status, body, etag=None):
        raw = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        if etag:
            self.send_header("ETag", etag)
        self.end_headers()
        self.wfile.write(raw)

    def error(self, status, code, message):
        self.reply(status, {"code": code, "message": message})

    def handle_any(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        body = json.loads(raw) if raw else None
        parts = urlsplit(self.path)
        path = unquote(parts.path).removeprefix("/api/v1")
        query = {key: values[0] for key, values in parse_qs(parts.query).items()}
        self.state.requests.append(
            {"method": self.command, "path": path, "query": query, "body": body,
             "if_match": self.headers.get("If-Match")}
        )

        if path == "/health":
            if self.state.healthy:
                return self.reply(200, {"status": "ok"})
            return self.error(503, "storage_unavailable", "storage backend unreachable")
        if self.headers.get("Authorization") != f"Bearer {TOKEN}":
            return self.error(401, "unauthorized", "a valid bearer token is required")
        if path == "/capabilities":
            return self.reply(200, {
                "api_version": "1.0.0", "server_version": "fake", "bundle_versions": [2],
                "checksum_algorithms": ["sha256"],
                "upload_limits": {
                    "max_manifest_entries": 100000, "max_file_bytes": 10737418240,
                    "max_bundle_bytes": 1099511627776, "upload_ttl_seconds": 86400,
                },
            })
        if path == "/pools":
            return self.reply(200, {"pools": [
                {"name": name, "title": pool["title"], "bundle_count": len(pool["bundles"])}
                for name, pool in self.state.pools.items()
            ]})
        match = re.fullmatch(r"/tasks/(.+)", path)
        if match:
            task = self.state.tasks.get(match[1])
            return self.reply(200, task) if task else self.error(404, "task_not_found", "no such task")

        match = re.fullmatch(r"/pools/([^/]+)(/.*)?", path)
        if not match or match[1] not in self.state.pools:
            return self.error(404, "pool_not_found", "no such pool")
        return self.handle_pool(match[1], match[2] or "", query, body)

    def handle_pool(self, name, rest, query, body):
        pool = self.state.pools[name]
        bundles = list(pool["bundles"].values())
        if rest == "":
            return self.reply(200, {"name": name, "title": pool["title"], "bundle_count": len(bundles)})
        if rest == "/stats":
            return self.reply(200, {
                "pool": name, "generated_at": "2026-11-02T14:51:06.509983212Z",
                "storage": {
                    "bundle_count": len(bundles), "file_count": 6, "content_bytes": 6144,
                    "metadata_bytes": 100, "total_bytes": 6244,
                    "filesystem_capacity_bytes": 1000000, "filesystem_available_bytes": 400000,
                },
                "integrity": {
                    "verified_bundles": 0, "failed_bundles": 0, "unchecked_bundles": len(bundles),
                    "last_verified_at": None,
                },
            })
        if rest == "/bundles" and self.command == "GET":
            return self.list_bundles(bundles, query)
        if rest == "/bundles/query" and self.command == "POST":
            return self.query_bundles(bundles, body)

        match = re.fullmatch(r"/bundles/([a-f0-9]{64})(/.*)?", rest)
        bundle = pool["bundles"].get(match[1]) if match else None
        if not bundle:
            return self.error(404, "bundle_not_found", "no such bundle")
        return self.handle_bundle(bundle, match[2] or "", query, body)

    def list_bundles(self, bundles, query):
        rows = bundles
        if "title" in query:
            rows = [b for b in rows if query["title"].lower() in b["title"].lower()]
        if "tag" in query:
            rows = [b for b in rows if query["tag"] in b["tags"]]
        if "author" in query:
            rows = [b for b in rows if b["author"] == query["author"]]
        if "verified" in query:
            rows = [b for b in rows if b["verified"] == (query["verified"] == "true")]
        key, reverse = sort_key(query.get("sort"))
        rows = sorted(rows, key=key, reverse=reverse)
        self.reply(200, paged(
            [summary(b) for b in rows], int(query.get("page", 1)), int(query.get("page_size", 50))
        ))

    def query_bundles(self, bundles, body):
        filters = {k: v for k, v in body.items() if k not in {"page", "page_size", "sort"}}
        if not filters:
            return self.error(400, "invalid_query", "at least one filter is required")
        results = []
        for bundle in bundles:
            if "title" in filters and filters["title"].lower() not in bundle["title"].lower():
                continue
            if "author" in filters and filters["author"] != bundle["author"]:
                continue
            if "tags" in filters and not set(filters["tags"]) <= set(bundle["tags"]):
                continue
            if "verified" in filters and filters["verified"] != bundle["verified"]:
                continue
            if "min_size_bytes" in filters and bundle["size_bytes"] < filters["min_size_bytes"]:
                continue
            matching = []
            if "file" in filters:
                matching = [f for f in bundle["files"] if filters["file"].get("path", "") in f["path"]]
                if not matching:
                    continue
            results.append({"bundle": summary(bundle), "matching_files": matching})
        self.reply(200, paged(results, body.get("page", 1), body.get("page_size", 50)))

    def handle_bundle(self, bundle, rest, query, body):
        if rest == "" and self.command == "GET":
            return self.reply(200, detail(bundle), revision(bundle))
        if rest == "/files":
            return self.reply(200, paged(
                bundle["files"], int(query.get("page", 1)), int(query.get("page_size", 50))
            ))
        if rest == "/verify" and self.command == "POST":
            return self.queue_verify(bundle)

        if self.headers.get("If-Match") != revision(bundle):
            return self.error(412, "revision_mismatch", "bundle changed")
        if rest == "" and self.command == "PATCH":
            bundle["title"] = body["title"]
            bundle["rev"] += 1
            return self.reply(200, detail(bundle), revision(bundle))
        if rest == "/tags" and self.command in {"POST", "DELETE"}:
            current = set(bundle["tags"])
            current = current | set(body["tags"]) if self.command == "POST" else current - set(body["tags"])
            bundle["tags"] = sorted(current)
            bundle["rev"] += 1
            return self.reply(200, {"tags": bundle["tags"], "total": len(current)}, revision(bundle))
        return self.error(404, "not_found", "no such route")

    def queue_verify(self, bundle):
        task_id = f"task-{len(self.state.tasks) + 1}"
        ok = not bundle["corrupt"]
        bundle["verified"] = ok
        bundle["last_checked"] = "2026-11-02T15:00:00.123456789+01:00"
        corrupted = [] if ok else [{
            "path": bundle["files"][0]["path"], "reason": "checksum_mismatch",
            "expected": sum_of("1"), "actual": sum_of("9"),
        }]
        self.state.tasks[task_id] = {
            "id": task_id, "type": "verify", "status": "done",
            "created_at": "2026-11-02T15:00:00Z", "finished_at": "2026-11-02T15:00:01Z",
            "result": {
                "verified": ok, "checked_at": bundle["last_checked"],
                "files_checked": len(bundle["files"]), "corrupted_files": corrupted,
            },
        }
        self.reply(202, {
            "id": task_id, "type": "verify", "status": "queued", "created_at": "2026-11-02T15:00:00Z",
        })

    do_GET = do_POST = do_PATCH = do_DELETE = handle_any


def start(port=0):
    """Start in a daemon thread. Returns (http server, shared state, API base URL)."""
    state = FakeState()
    handler = type("BoundHandler", (Handler,), {"state": state})
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, state, f"http://127.0.0.1:{server.server_address[1]}/api/v1"


if __name__ == "__main__":
    state = FakeState()
    handler = type("BoundHandler", (Handler,), {"state": state})
    ThreadingHTTPServer(("0.0.0.0", int(sys.argv[1])), handler).serve_forever()
