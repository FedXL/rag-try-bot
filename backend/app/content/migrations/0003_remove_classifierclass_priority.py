from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("content", "0002_remove_source_title"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="classifierclass",
            name="priority",
        ),
        migrations.AlterModelOptions(
            name="classifierclass",
            options={"ordering": ["slug"], "verbose_name": "Класс", "verbose_name_plural": "Классы"},
        ),
        migrations.AlterModelOptions(
            name="source",
            options={"ordering": ["classifier_class__slug", "id"], "verbose_name": "Источник", "verbose_name_plural": "Источники"},
        ),
    ]
