from django.db import models

from .client import BundleClient


class Server(models.Model):
    """A bundle-server instance the UI can talk to."""

    name = models.CharField(max_length=100, unique=True)
    base_url = models.CharField(max_length=500, help_text="API root, ending in /api/v1.")
    token = models.TextField(blank=True)
    managed_by_env = models.BooleanField(
        default=False, help_text="Defined by BUNDLE_SERVER_* variables; not editable in the UI."
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def client(self):
        return BundleClient(self.base_url, self.token)


class Pool(models.Model):
    class SyncStatus(models.TextChoices):
        NEVER = "never", "never synced"
        QUEUED = "queued", "queued"
        RUNNING = "running", "running"
        OK = "ok", "ok"
        ERROR = "error", "failed"

    server = models.ForeignKey(Server, on_delete=models.CASCADE, related_name="pools")
    name = models.CharField(max_length=64)
    title = models.CharField(max_length=256, blank=True)
    bundle_count = models.IntegerField(null=True, blank=True)
    sync_status = models.CharField(max_length=10, choices=SyncStatus.choices, default=SyncStatus.NEVER)
    sync_error = models.TextField(blank=True)
    sync_requested_at = models.DateTimeField(null=True, blank=True)
    last_synced_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["server", "name"], name="unique_pool_per_server")]

    def __str__(self):
        return f"{self.server.name}/{self.name}"


class Bundle(models.Model):
    """Local cache of a remote bundle, plus metadata that only exists locally."""

    pool = models.ForeignKey(Pool, on_delete=models.CASCADE, related_name="bundles")
    checksum = models.CharField(max_length=64)
    title = models.CharField(max_length=256, blank=True)
    author = models.CharField(max_length=256, blank=True)
    created_at = models.DateTimeField(null=True, blank=True)
    version = models.IntegerField(null=True, blank=True)
    file_count = models.IntegerField(default=0)
    size_bytes = models.BigIntegerField(default=0)
    verified = models.BooleanField(default=False)
    last_checked = models.DateTimeField(null=True, blank=True)
    revision = models.CharField(max_length=128, blank=True)
    seen_at = models.DateTimeField(null=True, blank=True)
    missing_remote = models.BooleanField(
        default=False, help_text="Gone from the server at last sync; kept so local metadata survives."
    )
    # Never sent to the server, which only allows the title to change.
    notes = models.TextField(blank=True)
    attributes = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["title", "checksum"]
        constraints = [models.UniqueConstraint(fields=["pool", "checksum"], name="unique_bundle_per_pool")]

    def __str__(self):
        return self.title or self.checksum[:12]

    def as_summary(self):
        """The API's BundleSummary shape, so templates can render cached and live rows alike."""
        return {
            "checksum": self.checksum,
            "title": self.title,
            "author": self.author,
            "created_at": self.created_at.isoformat() if self.created_at else "",
            "version": self.version,
            "file_count": self.file_count,
            "size_bytes": self.size_bytes,
            "verified": self.verified,
        }


class BundleTag(models.Model):
    bundle = models.ForeignKey(Bundle, on_delete=models.CASCADE, related_name="tags")
    name = models.CharField(max_length=64)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["bundle", "name"], name="unique_tag_per_bundle")]
        indexes = [models.Index(fields=["name"])]

    def __str__(self):
        return self.name


class VerificationRun(models.Model):
    class Status(models.TextChoices):
        QUEUED = "queued", "queued"
        RUNNING = "running", "running"
        DONE = "done", "done"
        FAILED = "failed", "failed"

    bundle = models.ForeignKey(Bundle, on_delete=models.CASCADE, related_name="verifications")
    task_id = models.CharField(max_length=128)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.QUEUED)
    verified = models.BooleanField(null=True)
    files_checked = models.IntegerField(null=True, blank=True)
    corrupted_files = models.JSONField(default=list, blank=True)
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    @property
    def finished(self):
        return self.status in {self.Status.DONE, self.Status.FAILED}
