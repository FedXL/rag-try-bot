from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0004_qaitem_classifier_class"),
    ]

    operations = [
        migrations.AddField(
            model_name="telegramuser",
            name="is_superuser",
            field=models.BooleanField(db_index=True, default=False),
        ),
    ]
