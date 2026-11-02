from django.urls import path, register_converter

from . import views


class ChecksumConverter:
    regex = "[a-f0-9]{64}"

    def to_python(self, value):
        return value

    def to_url(self, value):
        return value


register_converter(ChecksumConverter, "checksum")

SERVER = "s/<int:server_id>/"
POOL = SERVER + "p/<slug:pool>/"
BUNDLE = POOL + "b/<checksum:checksum>/"

urlpatterns = [
    path("", views.home, name="home"),
    path("healthz", views.healthz, name="healthz"),
    path("servers/", views.server_list, name="server_list"),
    path("servers/add/", views.server_edit, name="server_add"),
    path(SERVER + "edit/", views.server_edit, name="server_edit"),
    path(SERVER + "delete/", views.server_delete, name="server_delete"),
    path(SERVER, views.pool_list, name="pool_list"),
    path(SERVER + "status/", views.server_status, name="server_status"),
    path(POOL, views.bundle_list, name="bundle_list"),
    path(POOL + "query/", views.bundle_query, name="bundle_query"),
    path(POOL + "sync/", views.pool_sync, name="pool_sync"),
    path(POOL + "tags/", views.tag_list, name="tag_list"),
    path(POOL + "tags/rename/", views.pool_tag_rename, name="pool_tag_rename"),
    path(POOL + "tags/delete/", views.pool_tag_delete, name="pool_tag_delete"),
    path(POOL + "verifications/<int:run_id>/", views.verification_detail, name="verification_detail"),
    path(BUNDLE, views.bundle_detail, name="bundle_detail"),
    path(BUNDLE + "title/", views.bundle_title, name="bundle_title"),
    path(BUNDLE + "local/", views.bundle_local_metadata, name="bundle_local_metadata"),
    path(BUNDLE + "verify/", views.bundle_verify, name="bundle_verify"),
    path(BUNDLE + "tags/add/", views.bundle_tag_add, name="bundle_tag_add"),
    path(BUNDLE + "tags/remove/", views.bundle_tag_remove, name="bundle_tag_remove"),
    path(BUNDLE + "tags/rename/", views.bundle_tag_rename, name="bundle_tag_rename"),
]
