from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0005_telegramuser_is_superuser"),
    ]

    operations = [
        migrations.CreateModel(
            name="LLMRequestLog",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("request_id", models.CharField(db_index=True, default="-", max_length=128)),
                ("purpose", models.CharField(db_index=True, default="chat", max_length=100)),
                ("provider", models.CharField(default="xai", max_length=64)),
                ("model", models.CharField(db_index=True, max_length=255)),
                ("temperature", models.FloatField(blank=True, null=True)),
                ("request_messages", models.JSONField(blank=True, default=list)),
                ("request_payload", models.JSONField(blank=True, default=dict)),
                ("response_text", models.TextField(blank=True, default="")),
                ("response_payload", models.JSONField(blank=True, default=dict)),
                ("status", models.CharField(choices=[("success", "Success"), ("error", "Error")], db_index=True, default="success", max_length=32)),
                ("error", models.TextField(blank=True, default="")),
                ("duration_ms", models.IntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
            ],
            options={"ordering": ["-created_at"]},
        ),
    ]
