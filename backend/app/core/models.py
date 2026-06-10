from django.db import models


class TelegramUser(models.Model):
    telegram_id = models.BigIntegerField(unique=True, db_index=True)
    username = models.CharField(max_length=255, blank=True, default="")
    first_name = models.CharField(max_length=255, blank=True, default="")
    last_name = models.CharField(max_length=255, blank=True, default="")
    is_superuser = models.BooleanField(default=False, db_index=True)
    first_seen_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.username or str(self.telegram_id)


class ChatMessage(models.Model):
    ROLE_USER = "user"
    ROLE_ASSISTANT = "assistant"
    ROLE_CHOICES = [(ROLE_USER, "User"), (ROLE_ASSISTANT, "Assistant")]
    user = models.ForeignKey(TelegramUser, on_delete=models.CASCADE, related_name="messages")
    telegram_message_id = models.BigIntegerField(null=True, blank=True)
    role = models.CharField(max_length=32, choices=ROLE_CHOICES)
    text = models.TextField()
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["created_at"]


class LLMRequestLog(models.Model):
    STATUS_CHOICES = [
        ("success", "Success"),
        ("error", "Error"),
    ]

    request_id = models.CharField(max_length=128, db_index=True, default="-")
    purpose = models.CharField(max_length=100, db_index=True, default="chat")
    provider = models.CharField(max_length=64, default="xai")
    model = models.CharField(max_length=255, db_index=True)
    temperature = models.FloatField(null=True, blank=True)
    request_messages = models.JSONField(default=list, blank=True)
    request_payload = models.JSONField(default=dict, blank=True)
    response_text = models.TextField(blank=True, default="")
    response_payload = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, db_index=True, default="success")
    error = models.TextField(blank=True, default="")
    duration_ms = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.request_id} {self.purpose} {self.status} {self.model}"
