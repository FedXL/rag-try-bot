from __future__ import annotations

from typing import Any

import httpx
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from app.core.text import normalize_text
from app.products.models import Product
from app.products.service import build_ai_text, vector_literal


def product_ids_without_embeddings(limit: int) -> list[int]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            select id
            from products_product
            where is_active = true and embedding is null
            order by id
            limit %s
            """,
            [limit],
        )
        return [row[0] for row in cursor.fetchall()]


def save_embedding(product_id: int, vector: list[float]) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            "update products_product set embedding = %s::vector where id = %s",
            [vector_literal(vector), product_id],
        )


class Command(BaseCommand):
    help = "Build product ai_text and sync pgvector embeddings through ml-api."

    def add_arguments(self, parser):
        parser.add_argument("--batch-size", type=int, default=64)
        parser.add_argument("--limit", type=int, default=0)

    def handle(self, *args: Any, **options: Any) -> None:
        ml_api_url = getattr(settings, "ML_API_URL", "")
        if not ml_api_url:
            raise CommandError("ML_API_URL is not configured")

        batch_size = max(1, int(options["batch_size"]))
        remaining_limit = int(options["limit"] or 0)
        total = 0

        while True:
            current_limit = batch_size if remaining_limit <= 0 else min(batch_size, remaining_limit)
            ids = product_ids_without_embeddings(current_limit)
            if not ids:
                break
            products = list(Product.objects.filter(id__in=ids).order_by("id"))
            texts: list[str] = []
            for product in products:
                if not product.ai_text:
                    product.ai_text = build_ai_text(product)
                    product.normalized_text = product.normalized_text or normalize_text(product.ai_text)
                    product.save(update_fields=["ai_text", "normalized_text"])
                texts.append(product.ai_text)

            response = httpx.post(f"{ml_api_url.rstrip('/')}/embed", json={"texts": texts}, timeout=120)
            response.raise_for_status()
            vectors = response.json().get("vectors") or []
            if len(vectors) != len(products):
                raise CommandError(f"ml-api returned {len(vectors)} vectors for {len(products)} products")

            for product, vector in zip(products, vectors):
                save_embedding(product.id, vector)
                total += 1

            if remaining_limit > 0:
                remaining_limit -= len(products)
                if remaining_limit <= 0:
                    break

            self.stdout.write(f"synced={total}")

        self.stdout.write(self.style.SUCCESS(f"done synced={total}"))
