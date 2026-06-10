import re

from django.db import migrations, models
import django.db.models.deletion


CLASS_DEFINITIONS = [
    ("product", "Товары", "Товары, бренды, SKU, артикулы, цены и наличие.", "product", 10),
    ("help", "Помощь", "Доставка, оплата, возврат, заказ, самовывоз и гарантии.", "article", 20),
    ("about", "О магазине", "Кто магазин, чем занимается, ассортимент, качество и безопасность.", "article", 30),
    ("inspiration", "Вдохновение", "Идеи интерьера, вдохновение, тренды и примеры.", "article", 40),
    ("color_selection", "Подбор цвета", "Подбор цвета, колеровка, палитры и оттенки.", "article", 50),
    ("partners", "Партнерам", "Дизайнеры, строители, партнерство и сотрудничество.", "article", 60),
    ("glossary", "Глоссарий", "Термины и определения.", "article", 70),
    ("contacts", "Контакты", "Адреса, телефоны, режим работы и как добраться.", "article", 80),
    ("news_articles", "Новости и статьи", "Новости, статьи и обзоры.", "article", 90),
    ("mixed", "Смешанный", "Вопрос относится к нескольким классам или класс неясен.", "system", 900),
    ("none", "Нет поиска", "Поиск в базе знаний не нужен.", "system", 1000),
]

SECTION_CHOICES = [
    ("product", "Товары"),
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


def normalize_text(text: str) -> str:
    value = (text or "").lower().replace("ё", "е")
    value = re.sub(r"[^0-9a-zа-я]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def seed_classes(apps, schema_editor):
    ClassifierClass = apps.get_model("core", "ClassifierClass")
    for slug, title, description, kind, priority in CLASS_DEFINITIONS:
        ClassifierClass.objects.update_or_create(
            slug=slug,
            defaults={
                "title": title,
                "description": description,
                "kind": kind,
                "priority": priority,
                "is_active": True,
            },
        )


def detect_article_class(normalized: str, old_section: str) -> str:
    if old_section and old_section not in {"catalog", "product"}:
        return old_section
    marker_map = [
        ("help", ["доставка", "оплата", "возврат", "обмен", "заказ", "самовывоз", "гарантия"]),
        ("contacts", ["контакты", "адрес", "телефон", "режим работы", "как добраться"]),
        ("color_selection", ["подбор цвета", "колеровка", "колеровать", "оттенок", "палитра"]),
        ("partners", ["партнер", "партнерам", "сотрудничество", "дизайнер", "строитель", "прораб"]),
        ("glossary", ["глоссарий", "термин", "что значит", "что такое"]),
        ("inspiration", ["вдохновение", "идеи", "интерьер", "дизайн", "тренды", "сочетание"]),
        ("news_articles", ["новости", "статья", "статьи", "публикация", "обзор"]),
        ("about", ["кто вы", "о компании", "центр красок", "ассортимент", "качество", "безопасные"]),
    ]
    for slug, markers in marker_map:
        if any(normalize_text(marker) in normalized for marker in markers):
            return slug
    return "mixed"


def is_product_like(chunk) -> bool:
    text = f"{chunk.title}\n{chunk.chunk_text}\n{chunk.source_url}\n{chunk.article_key}".lower()
    if "/catalog/" in text:
        return True
    product_markers = [
        "sku",
        "артикул:",
        "артикул sku",
        "цена:",
        " kzt",
        "категория товара:",
        "расход упаковки:",
    ]
    hits = sum(1 for marker in product_markers if marker in text)
    return hits >= 2


def extract_sku(text: str) -> str:
    patterns = [
        r"\bSKU\s+([A-ZА-Я0-9_-]{4,})",
        r"Артикул\s*SKU\s+([A-ZА-Я0-9_-]{4,})",
        r"Артикул:\s*([A-ZА-Я0-9_-]{4,})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip().upper()
    return ""


def extract_price(text: str):
    match = re.search(r"Цена:\s*([0-9]+(?:[,.][0-9]+)?)\s*KZT", text, flags=re.IGNORECASE)
    if not match:
        return None
    return match.group(1).replace(",", ".")


def extract_field(text: str, label: str) -> str:
    match = re.search(rf"{label}:\s*([^.;\n]+)", text, flags=re.IGNORECASE)
    return match.group(1).strip()[:255] if match else ""


def migrate_content(apps, schema_editor):
    ClassifierClass = apps.get_model("core", "ClassifierClass")
    Article = apps.get_model("core", "Article")
    ArticleChunk = apps.get_model("core", "ArticleChunk")
    Product = apps.get_model("core", "Product")
    QuickPhrase = apps.get_model("core", "QuickPhrase")

    classes = {row.slug: row for row in ClassifierClass.objects.all()}
    mixed = classes["mixed"]

    for chunk in ArticleChunk.objects.order_by("id").iterator():
        text = f"{chunk.title}\n\n{chunk.chunk_text}".strip()
        normalized = normalize_text(text)
        if chunk.section == "catalog" and is_product_like(chunk):
            Product.objects.update_or_create(
                product_key=chunk.article_key,
                defaults={
                    "name": (chunk.title or chunk.article_key)[:500],
                    "sku": extract_sku(text),
                    "brand": extract_field(text, "Бренд"),
                    "category_name": extract_field(text, "Категория"),
                    "price_kzt": extract_price(text),
                    "source_url": chunk.source_url,
                    "description": chunk.chunk_text,
                    "normalized_text": normalized,
                    "metadata": {**(chunk.metadata or {}), "source_articlechunk_id": chunk.id},
                    "is_active": chunk.is_active,
                },
            )
            chunk.is_active = False
            chunk.metadata = {**(chunk.metadata or {}), "migrated_to": "product"}
            chunk.save(update_fields=["is_active", "metadata", "updated_at"])
            continue

        class_slug = detect_article_class(normalized, chunk.section)
        classifier_class = classes.get(class_slug) or mixed
        article, _ = Article.objects.update_or_create(
            article_key=chunk.article_key,
            defaults={
                "classifier_class": classifier_class,
                "title": chunk.title or chunk.article_key,
                "body": chunk.chunk_text,
                "source_url": chunk.source_url,
                "normalized_text": normalized,
                "metadata": {**(chunk.metadata or {}), "source_section": chunk.section},
                "is_active": chunk.is_active,
            },
        )
        chunk.article = article
        chunk.section = classifier_class.slug
        chunk.save(update_fields=["article", "section", "updated_at"])

    for quick in QuickPhrase.objects.order_by("id").iterator():
        article = Article.objects.filter(article_key=quick.article_key).first()
        if article:
            quick.article = article
            quick.section = article.classifier_class.slug
            quick.save(update_fields=["article", "section", "updated_at"])


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0002_articlechunk_quickphrase"),
    ]

    operations = [
        migrations.CreateModel(
            name="ClassifierClass",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("slug", models.SlugField(max_length=64, unique=True)),
                ("title", models.CharField(max_length=255)),
                ("description", models.TextField(blank=True, default="")),
                ("kind", models.CharField(choices=[("article", "Article"), ("product", "Product"), ("system", "System")], db_index=True, default="article", max_length=32)),
                ("priority", models.IntegerField(db_index=True, default=100)),
                ("is_active", models.BooleanField(db_index=True, default=True)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"ordering": ["priority", "slug"]},
        ),
        migrations.CreateModel(
            name="Article",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("article_key", models.CharField(db_index=True, max_length=255, unique=True)),
                ("title", models.CharField(max_length=500)),
                ("body", models.TextField(blank=True, default="")),
                ("source_url", models.URLField(blank=True, default="")),
                ("normalized_text", models.TextField(blank=True, default="")),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("is_active", models.BooleanField(db_index=True, default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("classifier_class", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="articles", to="core.classifierclass")),
            ],
            options={"ordering": ["classifier_class__priority", "article_key"]},
        ),
        migrations.CreateModel(
            name="Product",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("product_key", models.CharField(db_index=True, max_length=255, unique=True)),
                ("name", models.CharField(max_length=500)),
                ("sku", models.CharField(blank=True, db_index=True, default="", max_length=128)),
                ("brand", models.CharField(blank=True, db_index=True, default="", max_length=255)),
                ("category_name", models.CharField(blank=True, db_index=True, default="", max_length=255)),
                ("price_kzt", models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True)),
                ("source_url", models.URLField(blank=True, default="")),
                ("description", models.TextField(blank=True, default="")),
                ("normalized_text", models.TextField(blank=True, default="")),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("is_active", models.BooleanField(db_index=True, default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"ordering": ["name", "id"]},
        ),
        migrations.AlterField(
            model_name="articlechunk",
            name="section",
            field=models.CharField(choices=SECTION_CHOICES, db_index=True, max_length=64),
        ),
        migrations.AlterField(
            model_name="quickphrase",
            name="section",
            field=models.CharField(choices=SECTION_CHOICES, db_index=True, max_length=64),
        ),
        migrations.AddField(
            model_name="articlechunk",
            name="article",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="chunks", to="core.article"),
        ),
        migrations.AddField(
            model_name="quickphrase",
            name="article",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="quick_phrases", to="core.article"),
        ),
        migrations.RunPython(seed_classes, reverse_code=migrations.RunPython.noop),
        migrations.RunPython(migrate_content, reverse_code=migrations.RunPython.noop),
        migrations.RunSQL("alter table core_product add column if not exists embedding vector", reverse_sql="alter table core_product drop column if exists embedding"),
        migrations.RunSQL(
            "create index if not exists core_product_normalized_trgm_idx on core_product using gin (normalized_text gin_trgm_ops)",
            reverse_sql="drop index if exists core_product_normalized_trgm_idx",
        ),
    ]
