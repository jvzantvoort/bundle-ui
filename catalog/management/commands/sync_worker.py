import logging
import signal
import threading
import time

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import close_old_connections
from django.db.models import Q

from catalog import services
from catalog.client import ApiError
from catalog.models import Pool, Server

logger = logging.getLogger(__name__)
POLL_SECONDS = 3


def sync_one(pool):
    try:
        count = services.sync_pool(pool)
        logger.info("Synced %s: %d bundles", pool, count)
    except ApiError as exc:
        logger.warning("Sync of %s failed: %s", pool, exc.message)


def sync_everything():
    for server in Server.objects.all():
        try:
            pools = services.refresh_pools(server)
        except ApiError as exc:
            logger.warning("Cannot list pools on %s: %s", server, exc.message)
            continue
        for pool in pools:
            sync_one(pool)


def tick(next_full_sync):
    """Do whatever is due and return when the next full sync is due."""
    # At first start the web container may not have created the server yet. An empty
    # pass doesn't count, or the first real sync would wait a whole interval.
    if time.monotonic() >= next_full_sync and Server.objects.exists():
        next_full_sync = time.monotonic() + settings.BUNDLE_SYNC_INTERVAL
        sync_everything()
    # Includes pools first seen in the UI since the last pass. A failed sync leaves
    # the "never" state, so an unreachable server isn't retried on every tick.
    due = Pool.objects.filter(
        Q(sync_requested_at__isnull=False) | Q(sync_status=Pool.SyncStatus.NEVER)
    ).select_related("server")
    for pool in due:
        sync_one(pool)
    return next_full_sync


class Command(BaseCommand):
    help = "Sync all pools periodically, and on request from the UI. Use --once for a single pass."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="Sync every pool once, then exit.")

    def handle(self, *args, **options):
        if options["once"]:
            sync_everything()
            return

        stop = threading.Event()
        # As PID 1 in a container, Python would otherwise ignore SIGTERM.
        signal.signal(signal.SIGTERM, lambda *_: stop.set())
        signal.signal(signal.SIGINT, lambda *_: stop.set())

        next_full_sync = 0.0
        while not stop.is_set():
            close_old_connections()
            try:
                next_full_sync = tick(next_full_sync)
            except Exception:
                # A database hiccup must not end the worker; try again on the next tick.
                logger.exception("Sync pass failed")
            stop.wait(POLL_SECONDS)
