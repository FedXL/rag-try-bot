from django.contrib import admin

from .models import Product


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("name", "sku", "brand", "category_name", "price_kzt", "availability", "can_buy", "is_active")
    list_filter = ("brand", "category_name", "availability", "can_buy", "is_active")
    search_fields = (
        "product_key",
        "site_product_id",
        "name",
        "sku",
        "brand",
        "category_name",
        "top_category",
        "leaf_category",
        "description",
        "full_description",
        "source_url",
    )
    readonly_fields = ("created_at", "updated_at", "parser_first_seen_at", "parser_last_seen_at", "parser_updated_at")
    ordering = ("name", "id")
