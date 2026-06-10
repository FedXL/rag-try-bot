from django.db import migrations, models


def copy_legacy_products(apps, schema_editor):
    LegacyProduct = apps.get_model("content", "Product")
    Product = apps.get_model("products", "Product")

    for item in LegacyProduct.objects.all().iterator():
        metadata = item.metadata or {}
        category_path = metadata.get("category_path") or ([item.category_name] if item.category_name else [])
        defaults = {
            "source_url": item.source_url or "",
            "site_product_id": str(metadata.get("site_product_id") or ""),
            "name": item.name,
            "sku": item.sku or "",
            "brand": item.brand or "",
            "category_name": item.category_name or "",
            "category_path": category_path,
            "top_category": metadata.get("top_category") or item.category_name or "",
            "leaf_category": metadata.get("leaf_category") or item.category_name or "",
            "category_quality": metadata.get("category_quality") or "legacy",
            "price_kzt": item.price_kzt,
            "old_price_kzt": metadata.get("old_price_kzt"),
            "discount_percent": metadata.get("discount_percent"),
            "currency": metadata.get("currency") or "KZT",
            "availability": metadata.get("availability") or "",
            "stock_by_city": metadata.get("stock_by_city") or {},
            "can_buy": metadata.get("can_buy"),
            "max_quantity": metadata.get("max_quantity"),
            "package_size": metadata.get("package_size") or "",
            "country": metadata.get("country") or "",
            "description": item.description or "",
            "full_description": metadata.get("full_description") or "",
            "characteristics": metadata.get("characteristics") or {},
            "image_urls": metadata.get("image_urls") or [],
            "image_metadata": metadata.get("image_metadata") or [],
            "variant_urls": metadata.get("variant_urls") or [],
            "related_urls": metadata.get("related_urls") or [],
            "raw_payload": metadata.get("raw_payload") or metadata,
            "normalized_text": item.normalized_text or "",
            "is_active": item.is_active,
        }
        Product.objects.update_or_create(product_key=item.product_key, defaults=defaults)


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("content", "0003_remove_classifierclass_priority"),
    ]

    operations = [
        migrations.CreateModel(
            name="Product",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("source_url", models.URLField(blank=True, db_index=True, default="", max_length=1000)),
                ("product_key", models.CharField(db_index=True, max_length=500, unique=True)),
                ("site_product_id", models.CharField(blank=True, db_index=True, default="", max_length=128)),
                ("name", models.CharField(max_length=500)),
                ("sku", models.CharField(blank=True, db_index=True, default="", max_length=128)),
                ("brand", models.CharField(blank=True, db_index=True, default="", max_length=255)),
                ("category_name", models.CharField(blank=True, db_index=True, default="", max_length=255)),
                ("category_path", models.JSONField(blank=True, default=list)),
                ("top_category", models.CharField(blank=True, db_index=True, default="", max_length=255)),
                ("leaf_category", models.CharField(blank=True, db_index=True, default="", max_length=255)),
                ("category_quality", models.CharField(blank=True, db_index=True, default="", max_length=64)),
                ("price_kzt", models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True)),
                ("old_price_kzt", models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True)),
                ("discount_percent", models.DecimalField(blank=True, decimal_places=2, max_digits=7, null=True)),
                ("currency", models.CharField(blank=True, default="KZT", max_length=16)),
                ("availability", models.CharField(blank=True, db_index=True, default="", max_length=64)),
                ("stock_by_city", models.JSONField(blank=True, default=dict)),
                ("can_buy", models.BooleanField(blank=True, db_index=True, null=True)),
                ("max_quantity", models.DecimalField(blank=True, decimal_places=3, max_digits=14, null=True)),
                ("package_size", models.CharField(blank=True, default="", max_length=255)),
                ("country", models.CharField(blank=True, default="", max_length=255)),
                ("description", models.TextField(blank=True, default="")),
                ("full_description", models.TextField(blank=True, default="")),
                ("characteristics", models.JSONField(blank=True, default=dict)),
                ("image_urls", models.JSONField(blank=True, default=list)),
                ("image_metadata", models.JSONField(blank=True, default=list)),
                ("variant_urls", models.JSONField(blank=True, default=list)),
                ("related_urls", models.JSONField(blank=True, default=list)),
                ("raw_payload", models.JSONField(blank=True, default=dict)),
                ("normalized_text", models.TextField(blank=True, default="")),
                ("is_active", models.BooleanField(db_index=True, default=True)),
                ("parser_first_seen_at", models.DateTimeField(blank=True, null=True)),
                ("parser_last_seen_at", models.DateTimeField(blank=True, null=True)),
                ("parser_updated_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "Товар",
                "verbose_name_plural": "Товары",
                "ordering": ["name", "id"],
            },
        ),
        migrations.AddIndex(
            model_name="product",
            index=models.Index(fields=["brand", "category_name"], name="products_pr_brand_ce9e43_idx"),
        ),
        migrations.AddIndex(
            model_name="product",
            index=models.Index(fields=["availability", "can_buy"], name="products_pr_availab_f5f7a5_idx"),
        ),
        migrations.RunPython(copy_legacy_products, migrations.RunPython.noop),
    ]
