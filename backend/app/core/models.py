from django.db import models


SECTION_CHOICES = [
    ("catalog", "Каталог"),
    ("help", "Помощь"),
    ("about", "О магазине"),
    ("inspiration", "Вдохновение"),
    ("color_selection", "Подбор цвета"),
    ("partners", "Партнерам"),
    ("glossary", "Глоссарий"),
    ("contacts", "Контакты"),
    ("news_articles", "Новости и статьи"),
    ("mixed", "Смешанный"),
    ("none", "Нет"),
]

SECTION_VALUES = {value for value, _ in SECTION_CHOICES}


class QAItem(models.Model):
    source_number = models.IntegerField(unique=True, db_index=True)
    question_ru = models.TextField()
    answer_ru = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["source_number"]

    def __str__(self):
        return f"#{self.source_number}: {self.question_ru[:80]}"


class ArticleChunk(models.Model):
    article_key = models.CharField(max_length=255, db_index=True)
    section = models.CharField(max_length=64, choices=SECTION_CHOICES, db_index=True)
    source_url = models.URLField(blank=True, default="")
    title = models.CharField(max_length=500, blank=True, default="")
    chunk_index = models.IntegerField(default=0)
    chunk_text = models.TextField()
    normalized_text = models.TextField(blank=True, default="")
    metadata = models.JSONField(default=dict, blank=True)
    is_active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["section", "article_key", "chunk_index"]
        constraints = [
            models.UniqueConstraint(fields=["article_key", "chunk_index"], name="unique_article_chunk"),
        ]

    def __str__(self):
        return f"{self.section}/{self.article_key}#{self.chunk_index}: {self.title[:80]}"


class QuickPhrase(models.Model):
    phrase = models.TextField()
    normalized_phrase = models.TextField(db_index=True)
    section = models.CharField(max_length=64, choices=SECTION_CHOICES, db_index=True)
    article_key = models.CharField(max_length=255, db_index=True)
    target_chunk_index = models.IntegerField(null=True, blank=True)
    priority = models.IntegerField(default=100, db_index=True)
    is_active = models.BooleanField(default=True, db_index=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["priority", "id"]
        constraints = [
            models.UniqueConstraint(fields=["normalized_phrase", "section", "article_key"], name="unique_quick_phrase_route"),
        ]

    def __str__(self):
        return f"{self.section}: {self.phrase[:100]}"


class SearchThresholdProfile(models.Model):
    name = models.CharField(max_length=100, unique=True)
    found_threshold = models.FloatField(default=0.2)
    not_found_threshold = models.FloatField(default=0.05)
    min_lexical_score = models.FloatField(default=0.08)
    active = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name


class TelegramUser(models.Model):
    telegram_id = models.BigIntegerField(unique=True, db_index=True)
    username = models.CharField(max_length=255, blank=True, default="")
    first_name = models.CharField(max_length=255, blank=True, default="")
    last_name = models.CharField(max_length=255, blank=True, default="")
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


class EmbeddingRun(models.Model):
    engine = models.CharField(max_length=100, unique=True)
    status = models.CharField(max_length=32, default="not_started")
    total = models.IntegerField(default=0)
    embedded = models.IntegerField(default=0)
    error = models.TextField(blank=True, default="")
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.engine}: {self.status}"
