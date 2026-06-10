from django.db import models


class Product(models.Model):
    source_url = models.URLField(max_length=1000, blank=True, default="", db_index=True)
    product_key = models.CharField(max_length=500, unique=True, db_index=True)
    site_product_id = models.CharField(max_length=128, blank=True, default="", db_index=True)
    name = models.CharField(max_length=500)
    sku = models.CharField(max_length=128, blank=True, default="", db_index=True)
    brand = models.CharField(max_length=500, blank=True, default="", db_index=True)

    category_name = models.CharField(max_length=500, blank=True, default="", db_index=True)
    category_path = models.JSONField(default=list, blank=True)
    top_category = models.CharField(max_length=500, blank=True, default="", db_index=True)
    leaf_category = models.CharField(max_length=500, blank=True, default="", db_index=True)
    category_quality = models.CharField(max_length=64, blank=True, default="", db_index=True)

    price_kzt = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    old_price_kzt = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    discount_percent = models.DecimalField(max_digits=7, decimal_places=2, null=True, blank=True)
    currency = models.CharField(max_length=16, blank=True, default="KZT")

    availability = models.CharField(max_length=64, blank=True, default="", db_index=True)
    stock_by_city = models.JSONField(default=dict, blank=True)
    can_buy = models.BooleanField(null=True, blank=True, db_index=True)
    max_quantity = models.DecimalField(max_digits=14, decimal_places=3, null=True, blank=True)
    package_size = models.CharField(max_length=500, blank=True, default="")
    country = models.CharField(max_length=500, blank=True, default="")

    description = models.TextField(blank=True, default="")
    full_description = models.TextField(blank=True, default="")
    characteristics = models.JSONField(default=dict, blank=True)
    image_urls = models.JSONField(default=list, blank=True)
    image_metadata = models.JSONField(default=list, blank=True)
    variant_urls = models.JSONField(default=list, blank=True)
    related_urls = models.JSONField(default=list, blank=True)
    raw_payload = models.JSONField(default=dict, blank=True)

    ai_text = models.TextField(blank=True, default="")
    normalized_text = models.TextField(blank=True, default="")
    is_active = models.BooleanField(default=True, db_index=True)
    parser_first_seen_at = models.DateTimeField(null=True, blank=True)
    parser_last_seen_at = models.DateTimeField(null=True, blank=True)
    parser_updated_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name", "id"]
        verbose_name = "Товар"
        verbose_name_plural = "Товары"
        indexes = [
            models.Index(fields=["brand", "category_name"]),
            models.Index(fields=["availability", "can_buy"]),
        ]

    def __str__(self):
        return self.name[:100]
