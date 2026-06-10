from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="ClassifierClass",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("slug", models.SlugField(max_length=64, unique=True)),
                ("title", models.CharField(max_length=255)),
                ("description", models.TextField(blank=True, default="")),
                ("kind", models.CharField(choices=[("source", "Source"), ("product", "Product"), ("system", "System")], db_index=True, default="source", max_length=32)),
                ("priority", models.IntegerField(db_index=True, default=100)),
                ("is_active", models.BooleanField(db_index=True, default=True)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "Класс",
                "verbose_name_plural": "Классы",
                "ordering": ["priority", "slug"],
            },
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
            options={
                "verbose_name": "Товар",
                "verbose_name_plural": "Товары",
                "ordering": ["name", "id"],
            },
        ),
        migrations.CreateModel(
            name="Source",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("source_key", models.CharField(db_index=True, max_length=255, unique=True)),
                ("source_number", models.IntegerField(blank=True, db_index=True, null=True, unique=True)),
                ("title", models.CharField(max_length=500)),
                ("body", models.TextField(blank=True, default="")),
                ("source_url", models.URLField(blank=True, default="")),
                ("normalized_text", models.TextField(blank=True, default="")),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("is_active", models.BooleanField(db_index=True, default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("classifier_class", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="sources", to="content.classifierclass")),
            ],
            options={
                "verbose_name": "Источник",
                "verbose_name_plural": "Источники",
                "ordering": ["classifier_class__priority", "source_number", "source_key"],
            },
        ),
        migrations.CreateModel(
            name="QuickPhrase",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("phrase", models.TextField()),
                ("normalized_phrase", models.TextField(db_index=True)),
                ("priority", models.IntegerField(db_index=True, default=100)),
                ("is_active", models.BooleanField(db_index=True, default=True)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("source", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="quick_phrases", to="content.source")),
            ],
            options={
                "verbose_name": "Быстрая фраза",
                "verbose_name_plural": "Быстрые фразы",
                "ordering": ["priority", "id"],
            },
        ),
        migrations.AddConstraint(
            model_name="quickphrase",
            constraint=models.UniqueConstraint(fields=("source", "normalized_phrase"), name="unique_source_quick_phrase"),
        ),
    ]
