from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("products", "0001_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="product",
            name="brand",
            field=models.CharField(blank=True, db_index=True, default="", max_length=500),
        ),
        migrations.AlterField(
            model_name="product",
            name="category_name",
            field=models.CharField(blank=True, db_index=True, default="", max_length=500),
        ),
        migrations.AlterField(
            model_name="product",
            name="top_category",
            field=models.CharField(blank=True, db_index=True, default="", max_length=500),
        ),
        migrations.AlterField(
            model_name="product",
            name="leaf_category",
            field=models.CharField(blank=True, db_index=True, default="", max_length=500),
        ),
        migrations.AlterField(
            model_name="product",
            name="package_size",
            field=models.CharField(blank=True, default="", max_length=500),
        ),
        migrations.AlterField(
            model_name="product",
            name="country",
            field=models.CharField(blank=True, default="", max_length=500),
        ),
    ]
