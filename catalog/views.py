import time
from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_not_required
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import services
from .client import ApiError, token_expiry
from .forms import (
    AddTagForm,
    BundleFilterForm,
    BundleQueryForm,
    DeletePoolTagForm,
    LocalMetadataForm,
    PoolRenameTagForm,
    RemoveTagForm,
    RenameTagForm,
    ServerForm,
    TitleForm,
    format_attributes,
)
from .models import Bundle, BundleTag, Pool, Server, VerificationRun

PAGE_SIZE = 50


def explain(exc):
    """Turn an API failure into something the user can act on."""
    if exc.unreachable:
        return exc.message
    if exc.status == 412:
        return "The bundle changed after this page loaded, so nothing was saved. Review it and try again."
    if exc.status == 401:
        return "The server rejected the API token. Update the token for this server."
    if exc.status == 403:
        return f"The API token may not do this: {exc.message}"
    return exc.message


def select(request, server, pool_name=None):
    """Remember the selection, dropping the pool when it belongs to another server."""
    if request.session.get("server_id") != server.pk:
        request.session.pop("pool", None)
    request.session["server_id"] = server.pk
    if pool_name:
        request.session["pool"] = pool_name


def page_number(request):
    try:
        return max(int(request.GET.get("page", 1)), 1)
    except ValueError:
        return 1


def pagination(request, page, page_size, total):
    def link(number):
        params = request.GET.copy()
        params["page"] = number
        return "?" + params.urlencode()

    pages = max((total + page_size - 1) // page_size, 1)
    return {
        "page": page,
        "pages": pages,
        "total": total,
        "prev": link(page - 1) if page > 1 else None,
        "next": link(page + 1) if page < pages else None,
    }


def resolve_pool(server, name):
    """Find a pool in the cache, or ask the server about one not seen before."""
    pool = Pool.objects.filter(server=server, name=name).first()
    if pool:
        return pool
    try:
        item = server.client().get_pool(name)
    except ApiError as exc:
        raise Http404(f"Pool {name} is not available: {exc.message}") from exc
    return Pool.objects.create(
        server=server, name=item["name"], title=item.get("title", ""), bundle_count=item.get("bundle_count")
    )


def pool_tag_names(pool):
    return (
        BundleTag.objects.filter(bundle__pool=pool, bundle__missing_remote=False)
        .values_list("name", flat=True)
        .distinct()
        .order_by("name")
    )


@login_not_required
def healthz(request):
    return HttpResponse("ok", content_type="text/plain")


def home(request):
    server_id = request.session.get("server_id")
    if server_id and Server.objects.filter(pk=server_id).exists():
        if request.session.get("pool"):
            return redirect("bundle_list", server_id, request.session["pool"])
        return redirect("pool_list", server_id)
    servers = list(Server.objects.all()[:2])
    if len(servers) == 1:
        return redirect("pool_list", servers[0].pk)
    return redirect("server_list")


# --- Servers -----------------------------------------------------------------------


def server_list(request):
    return render(request, "catalog/server_list.html", {"servers": Server.objects.all()})


def server_edit(request, server_id=None):
    server = get_object_or_404(Server, pk=server_id) if server_id else None
    if server and server.managed_by_env:
        messages.error(request, "This server is defined by BUNDLE_SERVER_* variables; change it in .env.")
        return redirect("server_list")
    form = ServerForm(request.POST or None, instance=server)
    if request.method == "POST" and form.is_valid():
        saved = form.save()
        messages.success(request, f"Saved server {saved.name}.")
        return redirect("pool_list", saved.pk)
    return render(request, "catalog/server_form.html", {"form": form, "server": server})


@require_POST
def server_delete(request, server_id):
    server = get_object_or_404(Server, pk=server_id, managed_by_env=False)
    if request.session.get("server_id") == server.pk:
        request.session.pop("server_id", None)
        request.session.pop("pool", None)
    server.delete()
    messages.success(request, f"Removed server {server.name} and its cached data.")
    return redirect("server_list")


def pool_list(request, server_id):
    server = get_object_or_404(Server, pk=server_id)
    select(request, server)
    try:
        pools = services.refresh_pools(server)
    except ApiError as exc:
        messages.warning(request, f"Showing cached pools. {explain(exc)}")
        pools = list(server.pools.all())
    return render(request, "catalog/pool_list.html", {"server": server, "pools": pools})


def server_status(request, server_id):
    server = get_object_or_404(Server, pk=server_id)
    select(request, server)
    client = server.client()
    now = timezone.now()
    expiry = token_expiry(server.token)
    context = {
        "server": server,
        "auto": request.GET.get("auto") == "1",
        "checked_at": now,
        "token_expiry": expiry,
        "token_expired": bool(expiry and expiry <= now),
    }

    started = time.monotonic()
    try:
        context["health"] = client.health()
    except ApiError as exc:
        context["health_error"] = explain(exc)
    context["latency_ms"] = round((time.monotonic() - started) * 1000)

    try:
        context["capabilities"] = client.capabilities()
    except ApiError as exc:
        context["capabilities_error"] = explain(exc)

    pool_stats = []
    try:
        for pool in services.refresh_pools(server):
            entry = {"pool": pool}
            try:
                entry["stats"] = client.pool_stats(pool.name)
                storage = entry["stats"]["storage"]
                capacity = storage.get("filesystem_capacity_bytes")
                available = storage.get("filesystem_available_bytes")
                if capacity and available is not None:
                    entry["used_percent"] = round((capacity - available) * 100 / capacity)
            except ApiError as exc:
                entry["error"] = explain(exc)
            pool_stats.append(entry)
    except ApiError as exc:
        context["pools_error"] = explain(exc)
        pool_stats = [{"pool": pool} for pool in server.pools.all()]
    context["pool_stats"] = pool_stats
    return render(request, "catalog/server_status.html", context)


# --- Browsing ----------------------------------------------------------------------


def cached_bundles(pool, filters, page):
    """Answer a bundle listing from the database while the server is unavailable."""
    queryset = pool.bundles.filter(missing_remote=False)
    if filters.get("title"):
        queryset = queryset.filter(title__icontains=filters["title"])
    if filters.get("tag"):
        queryset = queryset.filter(tags__name=filters["tag"])
    if filters.get("author"):
        queryset = queryset.filter(author=filters["author"])
    if filters.get("verified"):
        queryset = queryset.filter(verified=filters["verified"] == "true")
    queryset = queryset.order_by(filters.get("sort") or "title", "checksum")
    paginator = Paginator(queryset, PAGE_SIZE)
    rows = [bundle.as_summary() for bundle in paginator.get_page(page).object_list]
    return rows, paginator.count


def bundle_list(request, server_id, pool):
    server = get_object_or_404(Server, pk=server_id)
    pool_obj = resolve_pool(server, pool)
    select(request, server, pool_obj.name)

    form = BundleFilterForm(request.GET)
    filters = form.cleaned_data if form.is_valid() else {}
    page = page_number(request)
    offline = False
    try:
        data = server.client().list_bundles(
            pool_obj.name,
            page=page,
            page_size=PAGE_SIZE,
            title=filters.get("title"),
            tag=filters.get("tag"),
            author=filters.get("author"),
            verified=filters.get("verified"),
            sort=filters.get("sort"),
        )
        rows, total = data["items"], data["total"]
        services.upsert_summaries(pool_obj, rows)
    except ApiError as exc:
        if exc.status == 404:
            raise Http404(exc.message) from exc
        offline = True
        messages.warning(request, f"Showing cached bundles. {explain(exc)}")
        rows, total = cached_bundles(pool_obj, filters, page)

    services.attach_cached_tags(pool_obj, rows)
    return render(
        request,
        "catalog/bundle_list.html",
        {
            "server": server,
            "pool": pool_obj,
            "form": form,
            "rows": rows,
            "offline": offline,
            "pager": pagination(request, page, PAGE_SIZE, total),
            "tag_names": pool_tag_names(pool_obj),
        },
    )


def bundle_query(request, server_id, pool):
    server = get_object_or_404(Server, pk=server_id)
    pool_obj = resolve_pool(server, pool)
    select(request, server, pool_obj.name)

    form = BundleQueryForm(request.GET if request.GET else None)
    context = {"server": server, "pool": pool_obj, "form": form, "tag_names": pool_tag_names(pool_obj)}
    if form.is_bound and form.is_valid():
        payload = form.to_payload()
        if not payload:
            messages.info(request, "Fill in at least one filter.")
        else:
            page = page_number(request)
            payload.update(page=page, page_size=PAGE_SIZE)
            if form.cleaned_data["sort"]:
                payload["sort"] = form.cleaned_data["sort"]
            try:
                data = server.client().query_bundles(pool_obj.name, payload)
                services.upsert_summaries(pool_obj, [item["bundle"] for item in data["items"]])
                rows = [
                    {**item["bundle"], "matching_files": item.get("matching_files", [])}
                    for item in data["items"]
                ]
                services.attach_cached_tags(pool_obj, rows)
                context.update(
                    rows=rows, ran=True, pager=pagination(request, page, PAGE_SIZE, data["total"])
                )
            except ApiError as exc:
                messages.error(request, explain(exc))
    return render(request, "catalog/bundle_query.html", context)


def bundle_detail(request, server_id, pool, checksum):
    server = get_object_or_404(Server, pk=server_id)
    pool_obj = resolve_pool(server, pool)
    select(request, server, pool_obj.name)
    client = server.client()
    page = page_number(request)
    context = {"server": server, "pool": pool_obj, "offline": False, "tag_names": pool_tag_names(pool_obj)}

    try:
        data, etag = client.get_bundle(pool_obj.name, checksum)
        bundle = services.upsert_detail(pool_obj, data, etag)
        files = client.list_files(pool_obj.name, checksum, page=page, page_size=PAGE_SIZE)
        context.update(
            remote=data,
            files=files["items"],
            pager=pagination(request, page, PAGE_SIZE, files["total"]),
        )
    except ApiError as exc:
        if exc.status == 404:
            Bundle.objects.filter(pool=pool_obj, checksum=checksum).update(missing_remote=True)
            raise Http404(exc.message) from exc
        bundle = Bundle.objects.filter(pool=pool_obj, checksum=checksum).first()
        if bundle is None:
            messages.error(request, explain(exc))
            return redirect("bundle_list", server.pk, pool_obj.name)
        messages.warning(request, f"Showing cached data; editing is unavailable. {explain(exc)}")
        context.update(remote=bundle.as_summary(), offline=True, files=[])

    context.update(
        bundle=bundle,
        tags=sorted(bundle.tags.values_list("name", flat=True)),
        local_form=LocalMetadataForm(
            initial={"notes": bundle.notes, "attributes": format_attributes(bundle.attributes)}
        ),
        runs=bundle.verifications.all()[:5],
    )
    return render(request, "catalog/bundle_detail.html", context)


# --- Bundle changes ----------------------------------------------------------------


def bundle_action(view):
    """POST-only bundle change. API failures become a message on the bundle page."""

    @require_POST
    @wraps(view)
    def wrapper(request, server_id, pool, checksum):
        server = get_object_or_404(Server, pk=server_id)
        bundle = get_object_or_404(Bundle, pool__server=server, pool__name=pool, checksum=checksum)
        try:
            response = view(request, bundle, server.client())
        except ApiError as exc:
            messages.error(request, explain(exc))
            response = None
        return response or redirect("bundle_detail", server.pk, pool, checksum)

    return wrapper


def form_errors(request, form):
    for errors in form.errors.values():
        for error in errors:
            messages.error(request, error)


@bundle_action
def bundle_title(request, bundle, client):
    form = TitleForm(request.POST)
    if not form.is_valid():
        form_errors(request, form)
        return None
    data, etag = client.update_title(
        bundle.pool.name, bundle.checksum, form.cleaned_data["title"], form.cleaned_data["revision"]
    )
    services.upsert_detail(bundle.pool, data, etag)
    messages.success(request, "Title updated.")
    return None


@bundle_action
def bundle_tag_add(request, bundle, client):
    form = AddTagForm(request.POST)
    if not form.is_valid():
        form_errors(request, form)
        return None
    result, etag = client.add_tags(
        bundle.pool.name, bundle.checksum, [form.cleaned_data["tag"]], form.cleaned_data["revision"]
    )
    services.set_tags(bundle, result["tags"])
    messages.success(request, f"Added tag {form.cleaned_data['tag']}.")
    return None


@bundle_action
def bundle_tag_remove(request, bundle, client):
    form = RemoveTagForm(request.POST)
    if not form.is_valid():
        form_errors(request, form)
        return None
    result, etag = client.remove_tags(
        bundle.pool.name, bundle.checksum, [form.cleaned_data["tag"]], form.cleaned_data["revision"]
    )
    services.set_tags(bundle, result["tags"])
    messages.success(request, f"Removed tag {form.cleaned_data['tag']}.")
    return None


@bundle_action
def bundle_tag_rename(request, bundle, client):
    form = RenameTagForm(request.POST)
    if not form.is_valid():
        form_errors(request, form)
        return None
    data = form.cleaned_data
    services.rename_bundle_tag(client, bundle.pool, bundle, data["old"], data["new"], data["revision"])
    messages.success(request, f"Renamed tag {data['old']} to {data['new']}.")
    return None


@bundle_action
def bundle_local_metadata(request, bundle, client):
    form = LocalMetadataForm(request.POST)
    if not form.is_valid():
        form_errors(request, form)
        return None
    bundle.notes = form.cleaned_data["notes"]
    bundle.attributes = form.cleaned_data["attributes"]
    bundle.save(update_fields=["notes", "attributes"])
    messages.success(request, "Local metadata saved.")
    return None


@bundle_action
def bundle_verify(request, bundle, client):
    task = client.verify(bundle.pool.name, bundle.checksum)
    run = VerificationRun.objects.create(bundle=bundle, task_id=task["id"], status=task["status"])
    return redirect("verification_detail", bundle.pool.server_id, bundle.pool.name, run.pk)


def verification_detail(request, server_id, pool, run_id):
    run = get_object_or_404(
        VerificationRun.objects.select_related("bundle__pool__server"),
        pk=run_id,
        bundle__pool__server_id=server_id,
        bundle__pool__name=pool,
    )
    services.refresh_verification(run)
    return render(
        request,
        "catalog/verification_detail.html",
        {"run": run, "bundle": run.bundle, "pool": run.bundle.pool, "server": run.bundle.pool.server},
    )


# --- Pool-wide tags and sync -------------------------------------------------------


def tag_list(request, server_id, pool):
    server = get_object_or_404(Server, pk=server_id)
    pool_obj = resolve_pool(server, pool)
    select(request, server, pool_obj.name)
    tags = (
        BundleTag.objects.filter(bundle__pool=pool_obj, bundle__missing_remote=False)
        .values("name")
        .annotate(count=Count("bundle", filter=Q(bundle__missing_remote=False)))
        .order_by("name")
    )
    return render(request, "catalog/tag_list.html", {"server": server, "pool": pool_obj, "tags": tags})


def report_pool_tag_change(request, verb, tag, changed, failures):
    if failures:
        shown = "; ".join(failures[:5])
        messages.error(request, f"{verb} {tag} on {changed} bundle(s), {len(failures)} failed: {shown}")
    else:
        messages.success(request, f"{verb} {tag} on {changed} bundle(s).")


@require_POST
def pool_tag_rename(request, server_id, pool):
    pool_obj = get_object_or_404(Pool, server_id=server_id, name=pool)
    form = PoolRenameTagForm(request.POST)
    if not form.is_valid():
        form_errors(request, form)
    else:
        old, new = form.cleaned_data["old"], form.cleaned_data["new"]
        try:
            changed, failures = services.change_tag_across_pool(pool_obj, old, new)
            report_pool_tag_change(request, "Renamed", old, changed, failures)
        except ApiError as exc:
            messages.error(request, explain(exc))
    return redirect("tag_list", server_id, pool)


@require_POST
def pool_tag_delete(request, server_id, pool):
    pool_obj = get_object_or_404(Pool, server_id=server_id, name=pool)
    form = DeletePoolTagForm(request.POST)
    if not form.is_valid():
        form_errors(request, form)
    else:
        old = form.cleaned_data["old"]
        try:
            changed, failures = services.change_tag_across_pool(pool_obj, old)
            report_pool_tag_change(request, "Deleted", old, changed, failures)
        except ApiError as exc:
            messages.error(request, explain(exc))
    return redirect("tag_list", server_id, pool)


@require_POST
def pool_sync(request, server_id, pool):
    pool_obj = get_object_or_404(Pool, server_id=server_id, name=pool)
    pool_obj.sync_requested_at = timezone.now()
    pool_obj.sync_status = Pool.SyncStatus.QUEUED
    pool_obj.save(update_fields=["sync_requested_at", "sync_status"])
    messages.info(request, "Sync queued; the worker picks it up within a few seconds.")
    target = "tag_list" if request.POST.get("next_section") == "tags" else "bundle_list"
    return redirect(target, server_id, pool)
