from django.core.management.base import BaseCommand

from app.content.models import CLASS_DEFINITIONS, ClassifierClass


class Command(BaseCommand):
    help = "Seed classifier classes. Sources and quick phrases are managed in the content admin."

    def handle(self, *args, **options):
        created = 0
        updated = 0
        for item in CLASS_DEFINITIONS:
            _, was_created = ClassifierClass.objects.update_or_create(
                slug=item["slug"],
                defaults={
                    "title": item["title"],
                    "description": item["description"],
                    "kind": item["kind"],
                    "is_active": True,
                },
            )
            if was_created:
                created += 1
            else:
                updated += 1
        self.stdout.write(self.style.SUCCESS(f"sync_initial_knowledge done: classes_created={created}, classes_updated={updated}"))
