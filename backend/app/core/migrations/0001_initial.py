from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True
    dependencies = []
    operations = [
        migrations.CreateModel(
            name="QAItem",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("source_number", models.IntegerField(db_index=True, unique=True)),
                ("question_ru", models.TextField()),
                ("answer_ru", models.TextField(blank=True, default="")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"ordering": ["source_number"]},
        ),
        migrations.CreateModel(
            name="SearchThresholdProfile",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=100, unique=True)),
                ("found_threshold", models.FloatField(default=0.2)),
                ("not_found_threshold", models.FloatField(default=0.05)),
                ("min_lexical_score", models.FloatField(default=0.08)),
                ("active", models.BooleanField(default=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
        ),
        migrations.CreateModel(
            name="TelegramUser",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("telegram_id", models.BigIntegerField(db_index=True, unique=True)),
                ("username", models.CharField(blank=True, default="", max_length=255)),
                ("first_name", models.CharField(blank=True, default="", max_length=255)),
                ("last_name", models.CharField(blank=True, default="", max_length=255)),
                ("first_seen_at", models.DateTimeField(auto_now_add=True)),
                ("last_seen_at", models.DateTimeField(auto_now=True)),
            ],
        ),
        migrations.CreateModel(
            name="EmbeddingRun",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("engine", models.CharField(max_length=100, unique=True)),
                ("status", models.CharField(default="not_started", max_length=32)),
                ("total", models.IntegerField(default=0)),
                ("embedded", models.IntegerField(default=0)),
                ("error", models.TextField(blank=True, default="")),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
        ),
        migrations.CreateModel(
            name="ChatMessage",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("telegram_message_id", models.BigIntegerField(blank=True, null=True)),
                ("role", models.CharField(choices=[("user", "User"), ("assistant", "Assistant")], max_length=32)),
                ("text", models.TextField()),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="messages", to="core.telegramuser")),
            ],
            options={"ordering": ["created_at"]},
        ),
    ]
