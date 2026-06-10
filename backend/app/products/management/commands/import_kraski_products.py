from __future__ import annotations

import json
import os
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable

import psycopg
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from psycopg.rows import dict_row

from app.core.text import normalize_text
from app.products.models import Product
from app.products.service import build_ai_text_from_values


PARSER_COLUMNS = [
    "source_url",
    "product_key",
    "site_product_id",
    "name",
    "sku",
    "brand",
    "category_name",
    "category_path",
    "top_category",
    "leaf_category",
    "category_quality",
    "price_kzt",
    "old_price_kzt",
    "discount_percent",
    "currency",
    "availability",
    "stock_by_city",
    "can_buy",
    "max_quantity",
    "package_size",
    "country",
    "description",
    "full_description",
    "characteristics",
    "image_urls",
    "image_metadata",
    "variant_urls",
    "related_urls",
    "raw_payload",
    "normalized_text",
    "first_seen_at",
    "last_seen_at",
    "updated_at",
]

JSON_DICT_FIELDS = {"stock_by_city", "characteristics", "raw_payload"}
JSON_LIST_FIELDS = {"category_path", "image_urls", "image_metadata", "variant_urls", "related_urls"}


def text(value: Any) -> str:
    return str(value or "").strip()


def decimal_or_none(value: Any) -> Decimal | None:
    if value in {None, ""}:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def bool_or_none(value: Any) -> bool | None:
    if value in {None, ""}:
        return None
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "t", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "f", "no", "n", "off"}:
        return False
    return None


def datetime_or_none(value: Any):
    if not value:
        return None
    if hasattr(value, "tzinfo"):
        return value if timezone.is_aware(value) else timezone.make_aware(value)
    parsed = parse_datetime(str(value))
    if not parsed:
        return None
    return parsed if timezone.is_aware(parsed) else timezone.make_aware(parsed)


def json_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if not value:
        return []
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            return [item.strip() for item in value.split(",") if item.strip()]
    return []


def build_search_text(data: dict[str, Any]) -> str:
    stock = json_dict(data.get("stock_by_city"))
    characteristics = json_dict(data.get("characteristics"))
    pieces = [
        data.get("name"),
        data.get("sku"),
        data.get("brand"),
        data.get("category_name"),
        data.get("top_category"),
        data.get("leaf_category"),
        " ".join(str(item) for item in json_list(data.get("category_path"))),
        data.get("price_kzt"),
        data.get("old_price_kzt"),
        data.get("discount_percent"),
        data.get("availability"),
        data.get("package_size"),
        data.get("country"),
        data.get("description"),
        data.get("full_description"),
        " ".join(f"{key} {value}" for key, value in stock.items()),
        " ".join(f"{key} {value}" for key, value in characteristics.items()),
    ]
    return normalize_text(" ".join(text(piece) for piece in pieces if piece not in {None, ""}))


def product_defaults(data: dict[str, Any]) -> dict[str, Any]:
    ai_text = build_ai_text_from_values(data)
    normalized = text(data.get("normalized_text")) or build_search_text(data)
    return {
        "source_url": text(data.get("source_url")),
        "site_product_id": text(data.get("site_product_id")),
        "name": text(data.get("name")),
        "sku": text(data.get("sku")),
        "brand": text(data.get("brand")),
        "category_name": text(data.get("category_name")),
        "category_path": json_list(data.get("category_path")),
        "top_category": text(data.get("top_category")),
        "leaf_category": text(data.get("leaf_category")),
        "category_quality": text(data.get("category_quality")),
        "price_kzt": decimal_or_none(data.get("price_kzt")),
        "old_price_kzt": decimal_or_none(data.get("old_price_kzt")),
        "discount_percent": decimal_or_none(data.get("discount_percent")),
        "currency": text(data.get("currency")) or "KZT",
        "availability": text(data.get("availability")),
        "stock_by_city": json_dict(data.get("stock_by_city")),
        "can_buy": bool_or_none(data.get("can_buy")),
        "max_quantity": decimal_or_none(data.get("max_quantity")),
        "package_size": text(data.get("package_size")),
        "country": text(data.get("country")),
        "description": text(data.get("description")),
        "full_description": text(data.get("full_description")),
        "characteristics": json_dict(data.get("characteristics")),
        "image_urls": json_list(data.get("image_urls")),
        "image_metadata": json_list(data.get("image_metadata")),
        "variant_urls": json_list(data.get("variant_urls")),
        "related_urls": json_list(data.get("related_urls")),
        "raw_payload": json_dict(data.get("raw_payload")),
        "ai_text": ai_text,
        "normalized_text": normalized,
        "parser_first_seen_at": datetime_or_none(data.get("first_seen_at")),
        "parser_last_seen_at": datetime_or_none(data.get("last_seen_at")),
        "parser_updated_at": datetime_or_none(data.get("updated_at")),
        "is_active": True,
    }


def rows_from_jsonl(path: str) -> Iterable[dict[str, Any]]:
    with open(path, "r", encoding="utf-8-sig") as stream:
        for number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as exc:
                raise CommandError(f"Bad JSON on line {number}: {exc}") from exc


def rows_from_database(database_url: str) -> Iterable[dict[str, Any]]:
    columns = ", ".join(PARSER_COLUMNS)
    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        with conn.cursor() as cursor:
            cursor.execute(f"select {columns} from products order by id")
            yield from cursor


class Command(BaseCommand):
    help = "Import products from kraski-parser Postgres or JSONL export into app.products."

    def add_arguments(self, parser):
        parser.add_argument("--database-url", default=os.environ.get("KRASKI_PARSER_DATABASE_URL", ""))
        parser.add_argument("--jsonl", default="")
        parser.add_argument("--no-deactivate", action="store_true")

    def handle(self, *args, **options):
        database_url = options["database_url"]
        jsonl = options["jsonl"]
        if bool(database_url) == bool(jsonl):
            raise CommandError("Pass exactly one source: --database-url or --jsonl.")

        rows = rows_from_jsonl(jsonl) if jsonl else rows_from_database(database_url)
        imported = 0
        skipped = 0
        active_keys: set[str] = set()

        with transaction.atomic():
            for data in rows:
                product_key = text(data.get("product_key")) or text(data.get("source_url"))
                name = text(data.get("name"))
                if not product_key or not name:
                    skipped += 1
                    continue
                active_keys.add(product_key)
                Product.objects.update_or_create(product_key=product_key, defaults=product_defaults(data))
                imported += 1

            deactivated = 0
            if not options["no_deactivate"]:
                deactivated = Product.objects.exclude(product_key__in=active_keys).update(is_active=False)

        self.stdout.write(
            self.style.SUCCESS(
                f"imported={imported} skipped={skipped} deactivated={deactivated} active_keys={len(active_keys)}"
            )
        )
