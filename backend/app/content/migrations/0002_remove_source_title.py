from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0007_content_app_refactor"),
        ("content", "0001_initial"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="source",
            name="title",
        ),
        migrations.RemoveField(
            model_name="source",
            name="source_key",
        ),
        migrations.RemoveField(
            model_name="source",
            name="source_number",
        ),
        migrations.RemoveField(
            model_name="source",
            name="normalized_text",
        ),
        migrations.RemoveField(
            model_name="source",
            name="metadata",
        ),
    ]
