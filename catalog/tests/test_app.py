from datetime import datetime, timezone
from unittest import mock

from django.contrib.auth import get_user_model
from django.conf import settings
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from catalog import services
from catalog.client import ApiError, normalize_base_url, token_expiry
from catalog.forms import BundleQueryForm, ByteSizeField, parse_attributes
from catalog.models import Bundle, Pool, Server, VerificationRun
from catalog.tests import fake_server
from catalog.tests.fake_server import TOKEN, sum_of

A = sum_of("a")
B = sum_of("b")
C = sum_of("c")


class HelperTests(SimpleTestCase):
    def test_normalize_base_url(self):
        expected = "http://host:8080/api/v1"
        for url in (
            "http://host:8080",
            "http://host:8080/",
            "http://host:8080/api/v1/",
            "http://host:8080/api/v1/pools/default",
        ):
            self.assertEqual(normalize_base_url(url), expected)

    def test_nanosecond_timestamp(self):
        parsed = services.parse_datetime("2026-11-02T14:08:17.721919611+01:00")
        self.assertEqual(parsed.astimezone(timezone.utc).hour, 13)
        self.assertIsNone(services.parse_datetime("garbage"))
        self.assertIsNone(services.parse_datetime(None))

    def test_token_expiry(self):
        token = "x.eyJleHAiOjE3OTM2Mjc3NTF9.y"  # {"exp":1793627751}
        self.assertEqual(token_expiry(token), datetime(2026, 11, 2, 13, 55, 51, tzinfo=timezone.utc))
        for bad in ("", "nonsense", "a.!!.c"):
            self.assertIsNone(token_expiry(bad))

    def test_byte_size(self):
        field = ByteSizeField(required=False)
        self.assertEqual(field.clean("1048576"), 1048576)
        self.assertEqual(field.clean("1.5 GB"), 1610612736)
        self.assertEqual(field.clean("500m"), 500 * 1024**2)
        self.assertIsNone(field.clean(""))
        with self.assertRaises(Exception):
            field.clean("big")

    def test_parse_attributes(self):
        self.assertEqual(parse_attributes("a = 1\n\nb=x=y"), {"a": "1", "b": "x=y"})
        with self.assertRaises(Exception):
            parse_attributes("no separator")

    def test_query_payload(self):
        form = BundleQueryForm({
            "title": "ice", "tags": "a\nb\na", "verified": "false", "min_size": "1 KB",
            "file_path": "x.jpg", "path_match": "suffix", "created_after": "2026-01-02T03:04",
        })
        self.assertTrue(form.is_valid(), form.errors)
        payload = form.to_payload()
        self.assertEqual(payload["tags"], ["a", "b"])
        self.assertIs(payload["verified"], False)
        self.assertEqual(payload["min_size_bytes"], 1024)
        self.assertEqual(payload["file"], {"path": "x.jpg", "path_match": "suffix"})
        self.assertTrue(payload["created_after"].startswith("2026-01-02T03:04:00"))

    def test_query_rejects_bad_checksum(self):
        self.assertFalse(BundleQueryForm({"checksums": "ABC"}).is_valid())


class FakeServerTestCase(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.http, cls.remote, cls.api_url = fake_server.start()

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown()
        cls.http.server_close()
        super().tearDownClass()

    def setUp(self):
        self.remote.reset()
        self.user = get_user_model().objects.create_user("tester", password="pw")
        self.client.force_login(self.user)
        self.server = Server.objects.create(name="fake", base_url=self.api_url, token=TOKEN)

    def url(self, name, *args):
        return reverse(name, args=[self.server.pk, *args])

    def open_bundle(self, checksum=A):
        response = self.client.get(self.url("bundle_detail", "default", checksum))
        self.assertEqual(response.status_code, 200)
        return Bundle.objects.get(pool__name="default", checksum=checksum)

    def go_offline(self):
        self.server.base_url = "http://127.0.0.1:9/api/v1"
        self.server.save()

    def writes(self):
        return [r for r in self.remote.requests if r["method"] != "GET"]

    def messages_of(self, response):
        return [str(m) for m in response.context["messages"]]


class AccessTests(FakeServerTestCase):
    def test_login_required_everywhere_but_healthz(self):
        self.client.logout()
        for url in (reverse("home"), reverse("server_list"), self.url("server_status")):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 302)
            self.assertIn("/login/", response["Location"])
        self.assertEqual(self.client.get(reverse("healthz")).status_code, 200)

    def test_writes_need_post(self):
        self.open_bundle()
        for name in ("bundle_title", "bundle_verify", "bundle_tag_add", "bundle_tag_remove"):
            response = self.client.get(self.url(name, "default", A))
            self.assertEqual(response.status_code, 405, name)
        self.assertEqual(self.writes(), [])


class OpenAccessTests(FakeServerTestCase):
    """REQUIRE_LOGIN=false: anonymous visitors get the full UI, but never the token."""

    def setUp(self):
        super().setUp()
        self.client.logout()
        middleware = [m for m in settings.MIDDLEWARE if "LoginRequired" not in m]
        override = override_settings(REQUIRE_LOGIN=False, MIDDLEWARE=middleware)
        override.enable()
        self.addCleanup(override.disable)

    def test_anonymous_gets_navigation_without_logout(self):
        response = self.client.get(self.url("bundle_list", "default"))
        self.assertContains(response, 'data-key="g q"')
        self.assertContains(response, "Iceland")
        self.assertNotContains(response, "Log out")

    def test_anonymous_can_write(self):
        self.client.get(self.url("bundle_detail", "default", A))
        revision = Bundle.objects.get(checksum=A).revision
        self.client.post(self.url("bundle_title", "default", A), {"title": "Open", "revision": revision})
        self.assertEqual(self.remote.pools["default"]["bundles"][A]["title"], "Open")

    def test_token_is_never_rendered(self):
        for url in (reverse("server_list"), self.url("server_edit"), self.url("server_status"), self.url("pool_list")):
            self.assertNotContains(self.client.get(url), TOKEN)


class ServerAndPoolTests(FakeServerTestCase):
    def test_add_server_normalizes_url_and_keeps_token_secret(self):
        response = self.client.post(reverse("server_add"), {
            "name": "new", "base_url": "http://host:8080/api/v1/pools/default", "token": "s3cret",
        })
        self.assertEqual(response.status_code, 302)
        server = Server.objects.get(name="new")
        self.assertEqual(server.base_url, "http://host:8080/api/v1")
        self.assertEqual(server.token, "s3cret")
        page = self.client.get(reverse("server_edit", args=[server.pk]))
        self.assertNotContains(page, "s3cret")

    def test_blank_token_on_edit_keeps_existing(self):
        self.client.post(reverse("server_edit", args=[self.server.pk]), {
            "name": "fake", "base_url": self.api_url, "token": "",
        })
        self.server.refresh_from_db()
        self.assertEqual(self.server.token, TOKEN)

    def test_rejects_non_http_url(self):
        response = self.client.post(reverse("server_add"), {"name": "x", "base_url": "file:///etc/passwd"})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Server.objects.filter(name="x").exists())

    def test_env_managed_server_is_locked(self):
        self.server.managed_by_env = True
        self.server.save()
        self.assertEqual(self.client.post(self.url("server_delete")).status_code, 404)
        self.assertEqual(self.client.get(self.url("server_edit")).status_code, 302)

    def test_selection_is_remembered(self):
        self.assertContains(self.client.get(self.url("pool_list")), "Archive")
        self.client.get(self.url("bundle_list", "archive"))
        response = self.client.get(reverse("home"))
        self.assertRedirects(response, self.url("bundle_list", "archive"))

    def test_selecting_other_server_clears_pool(self):
        self.client.get(self.url("bundle_list", "archive"))
        other = Server.objects.create(name="other", base_url="http://127.0.0.1:9/api/v1")
        self.client.get(reverse("pool_list", args=[other.pk]))
        self.assertIsNone(self.client.session.get("pool"))

    def test_bootstrap_from_environment(self):
        env = {
            "ADMIN_USERNAME": "boss", "ADMIN_PASSWORD": "pw12345678",
            "BUNDLE_SERVER_URL": "http://h:1/api/v1/pools/default",
            "BUNDLE_SERVER_NAME": "env", "BUNDLE_SERVER_TOKEN": "t1",
        }
        with mock.patch.dict("os.environ", env):
            call_command("bootstrap", verbosity=0, stdout=mock.Mock())
            env["BUNDLE_SERVER_TOKEN"] = "t2"
            with mock.patch.dict("os.environ", env):
                call_command("bootstrap", verbosity=0, stdout=mock.Mock())
        server = Server.objects.get(name="env")
        self.assertEqual((server.base_url, server.token), ("http://h:1/api/v1", "t2"))
        self.assertTrue(server.managed_by_env)
        self.assertTrue(get_user_model().objects.filter(username="boss", is_superuser=True).exists())


class BrowseTests(FakeServerTestCase):
    def test_list_shows_untitled_and_caches(self):
        response = self.client.get(self.url("bundle_list", "default"))
        self.assertContains(response, "Iceland")
        self.assertContains(response, "(untitled)")
        self.assertEqual(Bundle.objects.filter(pool__name="default").count(), 3)
        self.assertEqual(Bundle.objects.get(checksum=A).created_at.year, 2026)

    def test_filters_are_passed_to_server(self):
        response = self.client.get(self.url("bundle_list", "default"), {"title": "ice", "verified": "false"})
        self.assertContains(response, "Iceland")
        self.assertNotContains(response, "Corrupt one")
        last = self.remote.requests[-1]["query"]
        self.assertEqual((last["title"], last["verified"]), ("ice", "false"))
        self.assertNotIn("tag", last)

    def test_pagination_keeps_filters(self):
        from catalog.tests.fake_server import make_bundle

        bundles = self.remote.pools["default"]["bundles"]
        for number in range(60):
            bundle = make_bundle("d", f"item {number:02}")
            bundle["checksum"] = f"{number:064x}"
            bundles[bundle["checksum"]] = bundle
        response = self.client.get(self.url("bundle_list", "default"), {"title": "item"})
        self.assertEqual(response.context["pager"]["pages"], 2)
        self.assertEqual(response.context["pager"]["next"], "?title=item&page=2")
        self.assertIsNone(response.context["pager"]["prev"])

    def test_detail_shows_files_and_tags(self):
        response = self.client.get(self.url("bundle_detail", "default", A))
        self.assertContains(response, "photos/a-one.jpg")
        self.assertContains(response, "nature")
        self.assertEqual(Bundle.objects.get(checksum=A).revision, '"rev1"')

    def test_missing_bundle_404(self):
        self.client.get(self.url("bundle_list", "default"))
        del self.remote.pools["default"]["bundles"][A]
        response = self.client.get(self.url("bundle_detail", "default", A))
        self.assertEqual(response.status_code, 404)
        self.assertTrue(Bundle.objects.get(checksum=A).missing_remote)

    def test_unknown_pool_404(self):
        self.assertEqual(self.client.get(self.url("bundle_list", "nope")).status_code, 404)

    def test_html_in_titles_is_escaped(self):
        self.remote.pools["default"]["bundles"][A]["title"] = "<script>alert(1)</script>"
        response = self.client.get(self.url("bundle_list", "default"))
        self.assertNotContains(response, "<script>alert(1)")
        self.assertContains(response, "&lt;script&gt;alert(1)")


class OfflineTests(FakeServerTestCase):
    def setUp(self):
        super().setUp()
        services.sync_pool(services.refresh_pools(self.server)[0])

    def test_list_falls_back_to_cache_with_filters(self):
        self.go_offline()
        response = self.client.get(self.url("bundle_list", "default"), {"tag": "nature"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["offline"])
        self.assertContains(response, "Iceland")
        self.assertNotContains(response, "Corrupt one")
        self.assertContains(response, "Showing cached bundles")

    def test_detail_falls_back_read_only(self):
        self.go_offline()
        response = self.client.get(self.url("bundle_detail", "default", A))
        self.assertContains(response, "editing is unavailable")
        self.assertContains(response, "nature")
        self.assertNotContains(response, "bundle_title")
        self.assertNotContains(response, 'name="title"')

    def test_pools_fall_back(self):
        self.go_offline()
        self.assertContains(self.client.get(self.url("pool_list")), "Showing cached pools")

    def test_bad_token_is_explained(self):
        self.server.token = "wrong"
        self.server.save()
        self.assertContains(self.client.get(self.url("bundle_list", "default")), "rejected the API token")


class TitleTests(FakeServerTestCase):
    def post(self, title, revision):
        return self.client.post(
            self.url("bundle_title", "default", A), {"title": title, "revision": revision}, follow=True
        )

    def test_update(self):
        bundle = self.open_bundle()
        response = self.post("New name", bundle.revision)
        self.assertContains(response, "Title updated")
        self.assertEqual(self.remote.pools["default"]["bundles"][A]["title"], "New name")
        self.assertEqual(self.writes()[0]["if_match"], '"rev1"')
        bundle.refresh_from_db()
        self.assertEqual((bundle.title, bundle.revision), ("New name", '"rev2"'))

    def test_stale_revision_changes_nothing(self):
        bundle = self.open_bundle()
        self.remote.pools["default"]["bundles"][A]["rev"] = 5
        response = self.post("New name", bundle.revision)
        self.assertContains(response, "changed after this page loaded")
        self.assertEqual(self.remote.pools["default"]["bundles"][A]["title"], "Iceland")

    def test_title_can_be_cleared(self):
        bundle = self.open_bundle()
        self.post("", bundle.revision)
        self.assertEqual(self.remote.pools["default"]["bundles"][A]["title"], "")

    def test_overlong_title_is_rejected_locally(self):
        bundle = self.open_bundle()
        self.post("x" * 257, bundle.revision)
        self.assertEqual(self.writes(), [])


class TagTests(FakeServerTestCase):
    def remote_tags(self, checksum=A):
        return self.remote.pools["default"]["bundles"][checksum]["tags"]

    def local_tags(self, checksum=A):
        return sorted(Bundle.objects.get(checksum=checksum).tags.values_list("name", flat=True))

    def test_read(self):
        self.open_bundle()
        self.assertEqual(self.local_tags(), ["nature", "travel"])

    def test_add(self):
        bundle = self.open_bundle()
        self.client.post(self.url("bundle_tag_add", "default", A), {"tag": "new tag", "revision": bundle.revision})
        self.assertIn("new tag", self.remote_tags())
        self.assertIn("new tag", self.local_tags())

    def test_remove(self):
        bundle = self.open_bundle()
        self.client.post(self.url("bundle_tag_remove", "default", A), {"tag": "travel", "revision": bundle.revision})
        self.assertEqual(self.remote_tags(), ["nature"])
        self.assertEqual(self.local_tags(), ["nature"])

    def test_rename_uses_new_etag_for_second_step(self):
        bundle = self.open_bundle()
        response = self.client.post(
            self.url("bundle_tag_rename", "default", A),
            {"old": "travel", "new": "trips", "revision": bundle.revision}, follow=True,
        )
        self.assertContains(response, "Renamed tag")
        self.assertEqual(sorted(self.remote_tags()), ["nature", "trips"])
        self.assertEqual(self.local_tags(), ["nature", "trips"])
        self.assertEqual([w["if_match"] for w in self.writes()], ['"rev1"', '"rev2"'])
        # The next edit must work with the revision stored after the rename.
        response = self.client.get(self.url("bundle_detail", "default", A))
        self.assertContains(response, "&quot;rev3&quot;")

    def test_two_edits_from_same_page_second_conflicts(self):
        bundle = self.open_bundle()
        add = self.url("bundle_tag_add", "default", A)
        self.client.post(add, {"tag": "one", "revision": bundle.revision})
        response = self.client.post(add, {"tag": "two", "revision": bundle.revision}, follow=True)
        self.assertContains(response, "changed after this page loaded")
        self.assertNotIn("two", self.remote_tags())

    def test_newline_tag_rejected(self):
        bundle = self.open_bundle()
        self.client.post(self.url("bundle_tag_add", "default", A), {"tag": "a\nb", "revision": bundle.revision})
        self.assertEqual(self.writes(), [])

    def test_rename_to_same_name_keeps_the_tag(self):
        bundle = self.open_bundle()
        self.client.post(
            self.url("bundle_tag_rename", "default", A),
            {"old": "travel", "new": "travel", "revision": bundle.revision},
        )
        self.client.post(self.url("pool_tag_rename", "default"), {"old": "travel", "new": "travel"})
        self.assertIn("travel", self.remote_tags())
        self.assertEqual(self.writes(), [])

    def test_pool_wide_rename(self):
        self.client.get(self.url("tag_list", "default"))
        response = self.client.post(
            self.url("pool_tag_rename", "default"), {"old": "travel", "new": "trips"}, follow=True
        )
        self.assertContains(response, "Renamed travel on 2 bundle(s)")
        self.assertIn("trips", self.remote_tags(A))
        self.assertNotIn("travel", self.remote_tags(B))

    def test_pool_wide_delete_and_listing(self):
        services.sync_pool(services.refresh_pools(self.server)[0])
        page = self.client.get(self.url("tag_list", "default"))
        self.assertEqual({t["name"]: t["count"] for t in page.context["tags"]}, {"nature": 1, "travel": 2})
        self.client.post(self.url("pool_tag_delete", "default"), {"old": "travel"})
        self.assertEqual(self.remote_tags(B), [])
        page = self.client.get(self.url("tag_list", "default"))
        self.assertEqual({t["name"] for t in page.context["tags"]}, {"nature"})

    def test_pool_wide_failure_is_reported(self):
        original = services.rename_bundle_tag
        calls = []

        def flaky(client, pool, bundle, old, new, revision):
            calls.append(bundle.checksum)
            if len(calls) == 1:
                raise ApiError("boom", 500)
            return original(client, pool, bundle, old, new, revision)

        self.client.get(self.url("tag_list", "default"))
        with mock.patch.object(services, "rename_bundle_tag", flaky):
            response = self.client.post(
                self.url("pool_tag_rename", "default"), {"old": "travel", "new": "trips"}, follow=True
            )
        self.assertContains(response, "on 1 bundle(s), 1 failed")


class VerifyTests(FakeServerTestCase):
    def verify(self, checksum):
        self.open_bundle(checksum)
        return self.client.post(self.url("bundle_verify", "default", checksum), follow=True)

    def test_valid(self):
        response = self.verify(A)
        self.assertContains(response, "All 2 files match")
        bundle = Bundle.objects.get(checksum=A)
        self.assertTrue(bundle.verified)
        self.assertEqual(bundle.last_checked.year, 2026)
        self.assertNotContains(response, 'http-equiv="refresh"')

    def test_invalid_is_not_a_task_failure(self):
        response = self.verify(C)
        self.assertContains(response, "Invalid")
        self.assertContains(response, "checksum_mismatch")
        self.assertFalse(Bundle.objects.get(checksum=C).verified)
        self.assertEqual(VerificationRun.objects.get().status, "done")

    def test_pending_refreshes_then_finishes(self):
        self.open_bundle(A)
        self.client.post(self.url("bundle_verify", "default", A))
        run = VerificationRun.objects.get()
        task = self.remote.tasks[run.task_id]
        done = dict(task)
        task.clear()
        task.update(id=done["id"], type="verify", status="running", created_at=done["created_at"])
        url = self.url("verification_detail", "default", run.pk)
        self.assertContains(self.client.get(url), 'http-equiv="refresh"')
        task.update(done)
        self.assertNotContains(self.client.get(url), 'http-equiv="refresh"')

    def test_lost_task_ends_polling(self):
        self.open_bundle(A)
        self.client.post(self.url("bundle_verify", "default", A))
        self.remote.tasks.clear()
        run = VerificationRun.objects.get()
        response = self.client.get(self.url("verification_detail", "default", run.pk))
        self.assertContains(response, "no longer knows")

    def test_failed_task_does_not_change_verified_state(self):
        self.open_bundle(A)
        self.client.post(self.url("bundle_verify", "default", A))
        run = VerificationRun.objects.get()
        Bundle.objects.filter(checksum=A).update(verified=True)
        self.remote.tasks[run.task_id].update(status="failed", error="disk gone")
        self.remote.tasks[run.task_id].pop("result")
        response = self.client.get(self.url("verification_detail", "default", run.pk))
        self.assertContains(response, "disk gone")
        self.assertTrue(Bundle.objects.get(checksum=A).verified)

    def test_run_is_scoped_to_its_pool(self):
        self.open_bundle(A)
        self.client.post(self.url("bundle_verify", "default", A))
        run = VerificationRun.objects.get()
        Pool.objects.create(server=self.server, name="archive")
        response = self.client.get(self.url("verification_detail", "archive", run.pk))
        self.assertEqual(response.status_code, 404)


class QueryTests(FakeServerTestCase):
    def test_empty_form_does_not_call_server(self):
        response = self.client.get(self.url("bundle_query", "default"))
        self.assertEqual(response.status_code, 200)
        response = self.client.get(self.url("bundle_query", "default"), {"title": "", "sort": "title"})
        self.assertContains(response, "Fill in at least one filter")
        self.assertEqual(self.writes(), [])

    def test_file_query_shows_matches(self):
        response = self.client.get(self.url("bundle_query", "default"), {"file_path": "a-one"})
        self.assertContains(response, "photos/a-one.jpg")
        self.assertEqual(len(response.context["rows"]), 1)
        self.assertEqual(self.writes()[0]["body"]["file"], {"path": "a-one", "path_match": "contains"})

    def test_tags_are_anded(self):
        response = self.client.get(self.url("bundle_query", "default"), {"tags": "travel\nnature"})
        self.assertEqual([r["checksum"] for r in response.context["rows"]], [A])

    def test_server_error_is_shown(self):
        self.client.get(self.url("bundle_query", "default"))
        self.go_offline()
        response = self.client.get(self.url("bundle_query", "default"), {"title": "x"})
        self.assertContains(response, "Cannot reach")


class SyncTests(FakeServerTestCase):
    def pool(self):
        return Pool.objects.get(server=self.server, name="default")

    def test_sync_caches_tags_and_revision(self):
        services.refresh_pools(self.server)
        self.assertEqual(services.sync_pool(self.pool()), 3)
        pool = self.pool()
        self.assertEqual(pool.sync_status, "ok")
        self.assertIsNotNone(pool.last_synced_at)
        bundle = Bundle.objects.get(checksum=A)
        self.assertEqual(sorted(bundle.tags.values_list("name", flat=True)), ["nature", "travel"])
        self.assertEqual(bundle.revision, '"rev1"')

    def test_sync_is_read_only_on_server(self):
        services.refresh_pools(self.server)
        services.sync_pool(self.pool())
        self.assertEqual(self.writes(), [])

    def test_removed_bundle_is_flagged_and_keeps_notes(self):
        services.refresh_pools(self.server)
        services.sync_pool(self.pool())
        Bundle.objects.filter(checksum=A).update(notes="keep me")
        del self.remote.pools["default"]["bundles"][A]
        services.sync_pool(self.pool())
        bundle = Bundle.objects.get(checksum=A)
        self.assertTrue(bundle.missing_remote)
        self.assertEqual(bundle.notes, "keep me")

    def test_list_refresh_keeps_local_metadata(self):
        self.open_bundle()
        Bundle.objects.filter(checksum=A).update(notes="keep me", attributes={"k": "v"})
        self.client.get(self.url("bundle_list", "default"))
        bundle = Bundle.objects.get(checksum=A)
        self.assertEqual((bundle.notes, bundle.attributes, bundle.revision), ("keep me", {"k": "v"}, '"rev1"'))

    def test_failure_records_error_and_keeps_cache(self):
        services.refresh_pools(self.server)
        services.sync_pool(self.pool())
        self.go_offline()
        with self.assertRaises(ApiError):
            services.sync_pool(Pool.objects.get(server=self.server, name="default"))
        pool = self.pool()
        self.assertEqual(pool.sync_status, "error")
        self.assertIn("Cannot reach", pool.sync_error)
        self.assertFalse(Bundle.objects.get(checksum=A).missing_remote)

    def test_sync_button_queues(self):
        services.refresh_pools(self.server)
        self.client.post(self.url("pool_sync", "default"))
        pool = self.pool()
        self.assertEqual(pool.sync_status, "queued")
        self.assertIsNotNone(pool.sync_requested_at)

    def test_worker_retries_until_a_server_exists(self):
        from catalog.management.commands.sync_worker import tick

        Server.objects.all().delete()
        self.assertEqual(tick(0.0), 0.0)
        Server.objects.create(name="fake", base_url=self.api_url, token=TOKEN)
        self.assertGreater(tick(0.0), 0.0)
        self.assertEqual(Bundle.objects.filter(pool__name="default").count(), 3)

    def test_worker_syncs_new_pool_without_waiting_for_full_sync(self):
        from catalog.management.commands.sync_worker import tick

        services.refresh_pools(self.server)
        far_future = 1e12
        tick(far_future)
        self.assertEqual(self.pool().sync_status, "ok")

    def test_worker_does_not_retry_failed_pool_every_tick(self):
        from catalog.management.commands.sync_worker import tick

        services.refresh_pools(self.server)
        self.go_offline()
        with self.assertLogs("catalog.management.commands.sync_worker", "WARNING"):
            tick(1e12)
        self.assertEqual(self.pool().sync_status, "error")
        with self.assertNoLogs("catalog.management.commands.sync_worker", "WARNING"):
            tick(1e12)

    def test_sync_once_command(self):
        call_command("sync_worker", once=True)
        self.assertEqual(Bundle.objects.filter(pool__name="default").count(), 3)


class LocalMetadataTests(FakeServerTestCase):
    def test_save_is_local_only(self):
        self.open_bundle()
        response = self.client.post(
            self.url("bundle_local_metadata", "default", A),
            {"notes": "hello", "attributes": "place = Reykjavik"}, follow=True,
        )
        self.assertContains(response, "Reykjavik")
        bundle = Bundle.objects.get(checksum=A)
        self.assertEqual((bundle.notes, bundle.attributes), ("hello", {"place": "Reykjavik"}))
        self.assertEqual(self.writes(), [])

    def test_bad_attributes_change_nothing(self):
        self.open_bundle()
        self.client.post(self.url("bundle_local_metadata", "default", A), {"notes": "x", "attributes": "oops"})
        self.assertEqual(Bundle.objects.get(checksum=A).notes, "")


class StatusTests(FakeServerTestCase):
    def status(self):
        return self.client.get(self.url("server_status"))

    def test_healthy(self):
        response = self.status()
        self.assertContains(response, "fake")  # server version
        self.assertContains(response, "Default Pool")
        self.assertEqual(response.context["pool_stats"][0]["used_percent"], 60)

    def test_unhealthy_storage(self):
        self.remote.healthy = False
        self.assertContains(self.status(), "storage backend unreachable")

    def test_unreachable(self):
        self.go_offline()
        response = self.status()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "unavailable")

    def test_bad_token_but_healthy(self):
        self.server.token = "wrong"
        self.server.save()
        response = self.status()
        self.assertContains(response, "not working")
        self.assertContains(response, "rejected the API token")
        self.assertEqual(response.context["health"], {"status": "ok"})

    def test_expired_but_accepted_token_is_flagged(self):
        with mock.patch("catalog.views.token_expiry") as expiry:
            expiry.return_value = datetime(2020, 1, 1, tzinfo=timezone.utc)
            self.assertContains(self.status(), "server accepts it anyway")

    def test_auto_refresh_is_opt_in(self):
        self.assertNotContains(self.status(), 'http-equiv="refresh"')
        response = self.client.get(self.url("server_status") + "?auto=1")
        self.assertContains(response, 'http-equiv="refresh"')


class KeyboardMarkupTests(FakeServerTestCase):
    def test_shortcut_targets_are_present(self):
        pages = {
            self.url("bundle_list", "default"): ['data-key="/"', 'data-key="s"', "data-row", 'data-key="g q"'],
            self.url("bundle_detail", "default", A): [
                'data-key="v"', 'data-key="e"', 'data-key="t"', 'data-key="m"', 'data-key="b"',
            ],
            self.url("bundle_query", "default"): ['data-key="r"', 'data-key="/"'],
            self.url("server_status"): ['data-key="r"', 'data-key="a"', 'data-key="g h"'],
        }
        for url, needles in pages.items():
            response = self.client.get(url)
            for needle in needles:
                self.assertContains(response, needle, msg_prefix=url)
            self.assertContains(response, 'class="skip"')

    def test_no_duplicate_shortcuts_on_a_page(self):
        import re

        for url in (self.url("bundle_list", "default"), self.url("bundle_detail", "default", A)):
            html = self.client.get(url).content.decode()
            keys = re.findall(r'data-key="([^"]+)"', html)
            self.assertEqual(len(keys), len(set(keys)), keys)
