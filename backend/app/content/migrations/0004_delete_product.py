from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("products", "0001_initial"),
        ("content", "0003_remove_classifierclass_priority"),
    ]

    operations = [
        migrations.DeleteModel(
            name="Product",
        ),
    ]
