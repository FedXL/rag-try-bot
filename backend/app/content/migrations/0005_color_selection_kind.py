from django.db import migrations, models


def set_color_selection_kind(apps, schema_editor):
    ClassifierClass = apps.get_model("content", "ClassifierClass")
    ClassifierClass.objects.filter(slug="color_selection").update(kind="color_selection")


def reset_color_selection_kind(apps, schema_editor):
    ClassifierClass = apps.get_model("content", "ClassifierClass")
    ClassifierClass.objects.filter(slug="color_selection").update(kind="source")


class Migration(migrations.Migration):
    dependencies = [
        ("content", "0004_delete_product"),
    ]

    operations = [
        migrations.AlterField(
            model_name="classifierclass",
            name="kind",
            field=models.CharField(
                choices=[
                    ("source", "Source"),
                    ("product", "Product"),
                    ("color_selection", "Color selection"),
                    ("system", "System"),
                ],
                db_index=True,
                default="source",
                max_length=32,
            ),
        ),
        migrations.RunPython(set_color_selection_kind, reset_color_selection_kind),
    ]
