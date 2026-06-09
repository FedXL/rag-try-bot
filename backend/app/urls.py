from django.contrib import admin
from django.urls import path

from app.core import views

urlpatterns = [
    path("", views.index),
    path("admin/", admin.site.urls),
    path("api/health/", views.health),
    path("api/chat/", views.chat),
    path("api/search/", views.search),
    path("api/index/prepare/", views.prepare_index),
    path("api/tasks/<str:task_id>/", views.task_status),
    path("api/questions/", views.questions),
]
