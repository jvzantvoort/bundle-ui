import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from catalog.client import normalize_base_url
from catalog.models import Server


class Command(BaseCommand):
    help = "Create the first login and the server defined by the environment. Safe to re-run."

    def handle(self, *args, **options):
        username = os.environ.get("ADMIN_USERNAME")
        password = os.environ.get("ADMIN_PASSWORD")
        User = get_user_model()
        if username and password and not User.objects.filter(username=username).exists():
            User.objects.create_superuser(username=username, password=password)
            self.stdout.write(f"Created login {username}.")

        url = os.environ.get("BUNDLE_SERVER_URL")
        if url:
            name = os.environ.get("BUNDLE_SERVER_NAME", "default")
            Server.objects.update_or_create(
                name=name,
                defaults={
                    "base_url": normalize_base_url(url),
                    "token": os.environ.get("BUNDLE_SERVER_TOKEN", ""),
                    "managed_by_env": True,
                },
            )
            self.stdout.write(f"Configured server {name}.")
