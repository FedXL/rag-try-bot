from django.contrib import admin

from .models import ChatMessage, LLMRequestLog, TelegramUser


@admin.register(TelegramUser)
class TelegramUserAdmin(admin.ModelAdmin):
    list_display = ("telegram_id", "username", "first_name", "last_name", "is_superuser", "last_seen_at")
    list_editable = ("is_superuser",)
    list_filter = ("is_superuser",)
    search_fields = ("telegram_id", "username", "first_name", "last_name")
    ordering = ("-last_seen_at",)


@admin.register(ChatMessage)
class ChatMessageAdmin(admin.ModelAdmin):
    list_display = ("created_at", "user", "role", "telegram_message_id", "short_text")
    list_filter = ("role", "created_at")
    search_fields = ("user__telegram_id", "user__username", "text", "metadata")
    readonly_fields = ("user", "telegram_message_id", "role", "text", "metadata", "created_at")
    ordering = ("-created_at",)

    def short_text(self, obj):
        return obj.text[:160]


@admin.register(LLMRequestLog)
class LLMRequestLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "request_id", "purpose", "model", "status", "duration_ms")
    list_filter = ("purpose", "status", "model", "created_at")
    search_fields = ("request_id", "purpose", "model", "response_text", "error")
    readonly_fields = (
        "request_id",
        "purpose",
        "provider",
        "model",
        "temperature",
        "request_messages",
        "request_payload",
        "response_text",
        "response_payload",
        "status",
        "error",
        "duration_ms",
        "created_at",
    )
    ordering = ("-created_at",)
