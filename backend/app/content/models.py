from django.db import models


SECTION_CHOICES = [
    ("product", "Товары"),
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

CLASS_DEFINITIONS = [
    {"slug": "product", "title": "Товары", "description": "Товары, бренды, SKU, артикулы, цены и наличие.", "kind": "product"},
    {"slug": "help", "title": "Помощь", "description": "Доставка, оплата, возврат, заказ, самовывоз и гарантии.", "kind": "source"},
    {"slug": "about", "title": "О магазине", "description": "Кто магазин, чем занимается, ассортимент, качество и безопасность.", "kind": "source"},
    {"slug": "inspiration", "title": "Вдохновение", "description": "Идеи интерьера, вдохновение, тренды и примеры.", "kind": "source"},
    {"slug": "color_selection", "title": "Подбор цвета", "description": "Подбор цвета, колеровка, палитры и оттенки.", "kind": "color_selection"},
    {"slug": "partners", "title": "Партнерам", "description": "Дизайнеры, строители, партнерство и сотрудничество.", "kind": "source"},
    {"slug": "glossary", "title": "Глоссарий", "description": "Термины и определения.", "kind": "source"},
    {"slug": "contacts", "title": "Контакты", "description": "Адреса, телефоны, режим работы и как добраться.", "kind": "source"},
    {"slug": "news_articles", "title": "Новости и статьи", "description": "Новости, статьи и обзоры.", "kind": "source"},
    {"slug": "mixed", "title": "Смешанный", "description": "Вопрос относится к нескольким классам или класс неясен.", "kind": "system"},
    {"slug": "none", "title": "Нет поиска", "description": "Поиск в базе знаний не нужен.", "kind": "system"},
]


class ClassifierClass(models.Model):
    KIND_CHOICES = [
        ("source", "Source"),
        ("product", "Product"),
        ("color_selection", "Color selection"),
        ("system", "System"),
    ]

    slug = models.SlugField(max_length=64, unique=True)
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    kind = models.CharField(max_length=32, choices=KIND_CHOICES, default="source", db_index=True)
    is_active = models.BooleanField(default=True, db_index=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["slug"]
        verbose_name = "Класс"
        verbose_name_plural = "Классы"

    def __str__(self):
        return f"{self.slug}: {self.title}"


class Source(models.Model):
    classifier_class = models.ForeignKey(
        ClassifierClass,
        on_delete=models.PROTECT,
        related_name="sources",
        null=True,
        blank=True,
    )
    body = models.TextField(blank=True, default="")
    source_url = models.URLField(blank=True, default="")
    is_active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["classifier_class__slug", "id"]
        verbose_name = "Источник"
        verbose_name_plural = "Источники"

    def __str__(self):
        preview = " ".join((self.body or "").split())[:10]
        return f"{self.id} | {preview}" if preview else f"{self.id} |"


class QuickPhrase(models.Model):
    source = models.ForeignKey(Source, on_delete=models.CASCADE, related_name="quick_phrases")
    phrase = models.TextField()
    normalized_phrase = models.TextField(db_index=True)
    priority = models.IntegerField(default=100, db_index=True)
    is_active = models.BooleanField(default=True, db_index=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["priority", "id"]
        constraints = [
            models.UniqueConstraint(fields=["source", "normalized_phrase"], name="unique_source_quick_phrase"),
        ]
        verbose_name = "Быстрая фраза"
        verbose_name_plural = "Быстрые фразы"

    def __str__(self):
        return self.phrase[:120]
