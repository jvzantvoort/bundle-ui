from django.conf import settings

from .models import Server


def selection(request):
    """Expose the selected server and pool to the navigation."""
    user = getattr(request, "user", None)
    if settings.REQUIRE_LOGIN and not (user and user.is_authenticated):
        return {}
    match = request.resolver_match
    kwargs = match.kwargs if match else {}
    session = request.session
    server_id = kwargs.get("server_id") or session.get("server_id")
    server = Server.objects.filter(pk=server_id).first() if server_id else None
    pool = kwargs.get("pool")
    if not pool and server and session.get("server_id") == server.pk:
        pool = session.get("pool")
    return {
        "show_nav": True,
        "current_server": server,
        "current_pool": pool,
        "nav_section": match.url_name if match else "",
    }
