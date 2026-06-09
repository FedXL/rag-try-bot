from django.db import migrations, models


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


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="ArticleChunk",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("article_key", models.CharField(db_index=True, max_length=255)),
                ("section", models.CharField(choices=SECTION_CHOICES, db_index=True, max_length=64)),
                ("source_url", models.URLField(blank=True, default="")),
                ("title", models.CharField(blank=True, default="", max_length=500)),
                ("chunk_index", models.IntegerField(default=0)),
                ("chunk_text", models.TextField()),
                ("normalized_text", models.TextField(blank=True, default="")),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("is_active", models.BooleanField(db_index=True, default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"ordering": ["section", "article_key", "chunk_index"]},
        ),
        migrations.CreateModel(
            name="QuickPhrase",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("phrase", models.TextField()),
                ("normalized_phrase", models.TextField(db_index=True)),
                ("section", models.CharField(choices=SECTION_CHOICES, db_index=True, max_length=64)),
                ("article_key", models.CharField(db_index=True, max_length=255)),
                ("target_chunk_index", models.IntegerField(blank=True, null=True)),
                ("priority", models.IntegerField(db_index=True, default=100)),
                ("is_active", models.BooleanField(db_index=True, default=True)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"ordering": ["priority", "id"]},
        ),
        migrations.AddConstraint(
            model_name="articlechunk",
            constraint=models.UniqueConstraint(fields=("article_key", "chunk_index"), name="unique_article_chunk"),
        ),
        migrations.AddConstraint(
            model_name="quickphrase",
            constraint=models.UniqueConstraint(fields=("normalized_phrase", "section", "article_key"), name="unique_quick_phrase_route"),
        ),
        migrations.RunSQL("create extension if not exists vector", reverse_sql=migrations.RunSQL.noop),
        migrations.RunSQL("create extension if not exists pg_trgm", reverse_sql=migrations.RunSQL.noop),
        migrations.RunSQL(
            "alter table core_articlechunk add column if not exists embedding vector",
            reverse_sql="alter table core_articlechunk drop column if exists embedding",
        ),
        migrations.RunSQL(
            "create index if not exists core_articlechunk_normalized_trgm_idx on core_articlechunk using gin (normalized_text gin_trgm_ops)",
            reverse_sql="drop index if exists core_articlechunk_normalized_trgm_idx",
        ),
        migrations.RunSQL(
            "create index if not exists core_quickphrase_normalized_trgm_idx on core_quickphrase using gin (normalized_phrase gin_trgm_ops)",
            reverse_sql="drop index if exists core_quickphrase_normalized_trgm_idx",
        ),
    ]
