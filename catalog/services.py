"""Keeps the local database in step with the bundle-server."""
from datetime import datetime

from django.db import transaction
from django.utils import timezone

from .client import ApiError
from .models import Bundle, BundleTag, Pool, VerificationRun

SYNC_PAGE_SIZE = 100


def parse_datetime(value):
    """Parse an API timestamp (nanosecond precision is truncated). None if absent or malformed."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def refresh_pools(server):
    """Fetch the server's pools and cache them; returns the Pool rows that exist remotely."""
    rows = []
    for item in server.client().list_pools():
        pool, _ = Pool.objects.update_or_create(
            server=server,
            name=item["name"],
            defaults={"title": item.get("title", ""), "bundle_count": item.get("bundle_count")},
        )
        rows.append(pool)
    return rows


def upsert_summaries(pool, items):
    """Cache BundleSummary items. Tags, revision and local metadata are left untouched."""
    now = timezone.now()
    rows = [
        Bundle(
            pool=pool,
            checksum=item["checksum"],
            title=item.get("title", ""),
            author=item.get("author", ""),
            created_at=parse_datetime(item.get("created_at")),
            version=item.get("version"),
            file_count=item.get("file_count", 0),
            size_bytes=item.get("size_bytes", 0),
            verified=item.get("verified", False),
            seen_at=now,
            missing_remote=False,
        )
        for item in items
    ]
    Bundle.objects.bulk_create(
        rows,
        update_conflicts=True,
        unique_fields=["pool", "checksum"],
        update_fields=[
            "title", "author", "created_at", "version", "file_count",
            "size_bytes", "verified", "seen_at", "missing_remote",
        ],
    )


def set_tags(bundle, tags):
    wanted = set(tags)
    with transaction.atomic():
        bundle.tags.exclude(name__in=wanted).delete()
        BundleTag.objects.bulk_create(
            [BundleTag(bundle=bundle, name=name) for name in wanted], ignore_conflicts=True
        )


def upsert_detail(pool, item, etag=None):
    """Cache a full Bundle response, including its tags, and return the row."""
    upsert_summaries(pool, [item])
    bundle = Bundle.objects.get(pool=pool, checksum=item["checksum"])
    bundle.revision = etag or item.get("revision", "")
    bundle.last_checked = parse_datetime((item.get("state") or {}).get("last_checked"))
    bundle.save(update_fields=["revision", "last_checked"])
    set_tags(bundle, item.get("tags", []))
    return bundle


def attach_cached_tags(pool, rows):
    """List responses omit tags, so fill them in from the cache."""
    by_checksum = {}
    tags = BundleTag.objects.filter(
        bundle__pool=pool, bundle__checksum__in=[row["checksum"] for row in rows]
    ).values_list("bundle__checksum", "name")
    for checksum, name in tags:
        by_checksum.setdefault(checksum, []).append(name)
    for row in rows:
        row["tags"] = sorted(by_checksum.get(row["checksum"], []))
    return rows


def sync_pool(pool):
    """Mirror one pool into the database. Summaries omit tags, so each bundle is fetched too."""
    client = pool.server.client()
    Pool.objects.filter(pk=pool.pk).update(
        sync_status=Pool.SyncStatus.RUNNING, sync_requested_at=None, sync_error=""
    )
    try:
        seen = set()
        page = 1
        while True:
            # Oldest first: bundles published during the sync land on later pages.
            data = client.list_bundles(
                pool.name, page=page, page_size=SYNC_PAGE_SIZE, sort="created_at"
            )
            upsert_summaries(pool, data["items"])
            for item in data["items"]:
                seen.add(item["checksum"])
                try:
                    detail, etag = client.get_bundle(pool.name, item["checksum"])
                except ApiError as exc:
                    if exc.status != 404:
                        raise
                    seen.discard(item["checksum"])
                    continue
                upsert_detail(pool, detail, etag)
            if not data["items"] or page * SYNC_PAGE_SIZE >= data["total"]:
                break
            page += 1
        pool.bundles.exclude(checksum__in=seen).update(missing_remote=True)
    except ApiError as exc:
        Pool.objects.filter(pk=pool.pk).update(sync_status=Pool.SyncStatus.ERROR, sync_error=exc.message)
        raise
    Pool.objects.filter(pk=pool.pk).update(
        sync_status=Pool.SyncStatus.OK, last_synced_at=timezone.now(), bundle_count=len(seen)
    )
    return len(seen)


def rename_bundle_tag(client, pool, bundle, old, new, revision):
    """The API has no rename: add the new tag first so a failure can't lose the old one."""
    _, revision = client.add_tags(pool.name, bundle.checksum, [new], revision)
    result, revision = client.remove_tags(pool.name, bundle.checksum, [old], revision)
    set_tags(bundle, result["tags"])
    Bundle.objects.filter(pk=bundle.pk).update(revision=revision or "")


def checksums_with_tag(client, pool, tag):
    checksums = []
    page = 1
    while True:
        data = client.list_bundles(pool.name, tag=tag, page=page, page_size=SYNC_PAGE_SIZE)
        checksums.extend(item["checksum"] for item in data["items"])
        if not data["items"] or page * SYNC_PAGE_SIZE >= data["total"]:
            return checksums
        page += 1


def change_tag_across_pool(pool, old, new=None):
    """Rename `old` to `new` (or delete it when new is None) on every bundle carrying it.

    The bundles are listed up front because each change removes them from the tag's
    result set. Returns (changed count, list of "checksum: reason" failures).
    """
    client = pool.server.client()
    changed, failures = 0, []
    for checksum in checksums_with_tag(client, pool, old):
        try:
            detail, etag = client.get_bundle(pool.name, checksum)
            bundle = upsert_detail(pool, detail, etag)
            if new is None:
                result, revision = client.remove_tags(pool.name, checksum, [old], bundle.revision)
                set_tags(bundle, result["tags"])
                Bundle.objects.filter(pk=bundle.pk).update(revision=revision or "")
            else:
                rename_bundle_tag(client, pool, bundle, old, new, bundle.revision)
            changed += 1
        except ApiError as exc:
            failures.append(f"{checksum[:12]}: {exc.message}")
    return changed, failures


def refresh_verification(run):
    """Poll the task behind an unfinished run. Connection trouble leaves it pending."""
    if run.finished:
        return run
    bundle = run.bundle
    try:
        task = bundle.pool.server.client().task(run.task_id)
    except ApiError as exc:
        if exc.status != 404:
            return run
        # Tasks live on the server and may not outlast a restart.
        run.status = VerificationRun.Status.FAILED
        run.error = "The server no longer knows this task."
        run.finished_at = timezone.now()
        run.save()
        return run

    run.status = task["status"]
    if run.finished:
        run.finished_at = parse_datetime(task.get("finished_at")) or timezone.now()
        run.error = task.get("error", "")
        result = task.get("result")
        if result:
            run.verified = result["verified"]
            run.files_checked = result["files_checked"]
            run.corrupted_files = result["corrupted_files"]
            bundle.verified = result["verified"]
            bundle.last_checked = parse_datetime(result.get("checked_at")) or timezone.now()
            bundle.save(update_fields=["verified", "last_checked"])
    run.save()
    return run
