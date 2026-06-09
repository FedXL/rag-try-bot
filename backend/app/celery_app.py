import os
from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "app.settings")
app = Celery("telegram_rag_bot")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
