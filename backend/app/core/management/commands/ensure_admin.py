import os
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from app.core.models import SearchThresholdProfile


class Command(BaseCommand):
    def handle(self, *args, **kwargs):
        username = os.environ.get("DJANGO_SUPERUSER_USERNAME", "admin")
        password = os.environ.get("DJANGO_SUPERUSER_PASSWORD", "admin")
        User = get_user_model()
        user, _ = User.objects.get_or_create(username=username)
        user.is_staff = True
        user.is_superuser = True
        user.set_password(password)
        user.save()
        SearchThresholdProfile.objects.get_or_create(name="default", defaults={"active": True})
        self.stdout.write(self.style.SUCCESS(f"Admin ready: {username}"))
