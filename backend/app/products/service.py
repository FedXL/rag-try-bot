from __future__ import annotations

import json
import logging
import math
import re
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_CEILING
from time import perf_counter
from typing import Any, Callable

import httpx
from django.conf import settings
from django.db import connection
from django.db.models import Q, QuerySet

from app.core import llm
from app.core.text import normalize_text

from .models import Product

logger = logging.getLogger(__name__)

TOP_PRODUCTS = 5
HYBRID_PREFETCH = 40
DEFAULT_COATS = 2
DEFAULT_COVERAGE_M2_PER_LITER = Decimal("10")
PRODUCT_TRGM_THRESHOLD = 0.08

PRODUCT_ACTIONS = {
    "exact_product_lookup",
    "hybrid_product_search",
    "availability_check",
    "price_check",
    "discount_search",
    "compare_products",
    "budget_estimate",
    "recommend_products",
}

CITY_ALIASES = {
    "алматы": "Алматы",
    "астана": "Астана",
}

BRAND_ALIASES = {
    "dulux": ["dulux", "дулюкс"],
    "marshall": ["marshall", "маршал"],
    "dufa": ["dufa", "düfa", "дюфа", "дуфа"],
    "pinotex": ["pinotex", "пинотекс"],
    "hammerite": ["hammerite", "хаммерайт", "хамерайт"],
    "oikos": ["oikos", "ойкос"],
    "argile": ["argile", "аржиль"],
    "storch": ["storch", "шторх", "сторх"],
    "color expert": ["color expert", "колор эксперт"],
    "timbercare": ["timbercare", "тимберкеа"],
    "hygge": ["hygge", "хюгге"],
    "kelly-moore": ["kelly-moore", "kelly moore", "келли мур"],
    "tikkurila": ["tikkurila", "тиккурила", "тикурила"],
}

SKU_RE = re.compile(r"(?<![0-9a-zа-я])[0-9a-zа-я][0-9a-zа-я/_-]*\d{3,}[0-9a-zа-я/_-]*(?![0-9a-zа-я])", re.IGNORECASE)
AREA_RE = re.compile(r"(\d+(?:[,.]\d+)?)\s*(?:м2|м²|кв\.?\s*м|квадрат(?:ов|а|ные|ный)?)", re.IGNORECASE)
VOLUME_RE = re.compile(r"(\d+(?:[,.]\d+)?)\s*(?:л|литр(?:а|ов)?)\b", re.IGNORECASE)

INTENT_WORDS = {
    "price": ["цена", "стоимость", "сколько стоит", "почем", "по чем"],
    "stock": ["наличие", "есть ли", "в наличии", "остаток", "остатки", "сколько осталось", "где есть"],
    "discount": ["скидка", "скидки", "акция", "акции", "акционный", "дешевле", "дешево", "бюджетный"],
    "compare": ["сравни", "сравнить", "сравнение", "что дешевле", "какой дешевле", "между"],
    "budget": ["бюджет", "рассчитай", "расчитать", "рассчитать", "посчитай", "посчитать", "сколько выйдет", "на сколько хватит"],
    "recommend": ["подбери", "посоветуй", "порекомендуй", "что взять", "какую выбрать", "какой выбрать"],
}

STOP_WORDS = {
    "цена",
    "стоимость",
    "сколько",
    "стоит",
    "почем",
    "есть",
    "ли",
    "наличие",
    "наличии",
    "остатки",
    "остаток",
    "сравни",
    "сравнить",
    "сравнение",
    "бренд",
    "бренды",
    "товар",
    "товары",
    "какой",
    "какая",
    "какие",
    "что",
    "для",
    "по",
    "в",
    "на",
    "и",
    "или",
    "м2",
    "м",
    "кв",
    "квадратов",
}


@dataclass
class ProductPlan:
    action: str
    query: str
    sku: str = ""
    city: str = ""
    brands: list[str] = field(default_factory=list)
    category: str = ""
    surface: str = ""
    room: str = ""
    usage: str = ""
    budget_max: Decimal | None = None
    area_m2: Decimal | None = None
    volume_l: Decimal | None = None
    filters: dict[str, Any] = field(default_factory=dict)
    need_clarification: bool = False
    clarification_question: str = ""
    confidence: float = 0.0
    source: str = "rules"


@dataclass
class ToolResult:
    tool: str
    products: list[Product] = field(default_factory=list)
    answer: str = ""
    facts: dict[str, Any] = field(default_factory=dict)
    retrieval: dict[str, Any] = field(default_factory=dict)


def money(value: Any, currency: str = "KZT") -> str:
    if value in {None, ""}:
        return "-"
    amount = Decimal(str(value)).quantize(Decimal("1"), rounding=ROUND_CEILING)
    return f"{amount:,.0f}".replace(",", " ") + f" {currency or 'KZT'}"


def parse_decimal(value: Any) -> Decimal | None:
    if value in {None, ""}:
        return None
    try:
        return Decimal(str(value).replace(",", "."))
    except Exception:
        return None


def detect_brands(normalized: str) -> list[str]:
    brands: list[str] = []
    for canonical, aliases in BRAND_ALIASES.items():
        if any(normalize_text(alias) in normalized for alias in aliases):
            brands.append(canonical)
    return brands


def detect_city(normalized: str) -> str:
    for marker, canonical in CITY_ALIASES.items():
        if marker in normalized:
            return canonical
    return ""


def first_regex_decimal(pattern: re.Pattern[str], text: str) -> Decimal | None:
    match = pattern.search(text or "")
    return parse_decimal(match.group(1)) if match else None


def detect_budget_max(text: str) -> Decimal | None:
    normalized = normalize_text(text)
    match = re.search(r"(?:до|максимум|не дороже)\s*(\d+(?:[,.]\d+)?)\s*(?:к|тыс|k)?", normalized)
    if not match:
        return None
    value = parse_decimal(match.group(1))
    if value is None:
        return None
    suffix = match.group(0)
    return value * 1000 if any(marker in suffix for marker in ["к", "тыс", "k"]) and value < 1000 else value


def clean_query_text(normalized: str, brands: list[str], sku: str, city: str) -> str:
    value = normalized
    if sku:
        value = value.replace(normalize_text(sku), " ")
    if city:
        value = value.replace(normalize_text(city), " ")
    for brand in brands:
        for alias in BRAND_ALIASES.get(brand, [brand]):
            value = value.replace(normalize_text(alias), " ")
    value = AREA_RE.sub(" ", value)
    value = VOLUME_RE.sub(" ", value)
    words = [word for word in value.split() if word not in STOP_WORDS and not word.isdigit()]
    return " ".join(words).strip()


def infer_action(normalized: str, sku: str, brands: list[str], area_m2: Decimal | None) -> str:
    if area_m2 is not None or any(marker in normalized for marker in INTENT_WORDS["budget"]):
        return "budget_estimate"
    if len(brands) >= 2 or any(marker in normalized for marker in INTENT_WORDS["compare"]):
        return "compare_products"
    if any(marker in normalized for marker in INTENT_WORDS["discount"]):
        return "discount_search"
    if any(marker in normalized for marker in INTENT_WORDS["stock"]):
        return "availability_check"
    if any(marker in normalized for marker in INTENT_WORDS["price"]):
        return "price_check"
    if any(marker in normalized for marker in INTENT_WORDS["recommend"]):
        return "recommend_products"
    if sku:
        return "exact_product_lookup"
    return "hybrid_product_search"


def rule_plan(message: str, classification: dict[str, Any] | None = None) -> ProductPlan:
    normalized = normalize_text(message)
    slots = classification.get("slots", {}) if isinstance(classification, dict) else {}
    sku = str(slots.get("article_or_sku") or "").strip()
    if not sku:
        match = SKU_RE.search(message) or SKU_RE.search(normalized)
        sku = match.group(0).upper() if match else ""
    city = str(slots.get("city") or "").strip() or detect_city(normalized)
    brands = detect_brands(normalized)
    if slots.get("brand") and str(slots["brand"]).lower() not in brands:
        brands.append(str(slots["brand"]).lower())
    area_m2 = first_regex_decimal(AREA_RE, message)
    volume_l = first_regex_decimal(VOLUME_RE, message)
    action = infer_action(normalized, sku, brands, area_m2)
    query_text = clean_query_text(normalized, brands, sku, city)
    return ProductPlan(
        action=action,
        query=query_text or message,
        sku=sku,
        city=city,
        brands=brands[:3],
        area_m2=area_m2,
        volume_l=volume_l,
        budget_max=detect_budget_max(message),
        confidence=0.65,
        source="rules",
    )


def product_planner_prompt(message: str, history: list[dict[str, str]] | None, fallback: ProductPlan) -> str:
    history_text = "\n".join(f"{item['role']}: {item['content']}" for item in (history or [])[-6:])
    return f"""
Ты product planner для магазина красок. Не отвечай пользователю.
Верни JSON с одним action из списка:
exact_product_lookup, hybrid_product_search, availability_check, price_check, discount_search,
compare_products, budget_estimate, recommend_products.

Правила:
- Если есть артикул/SKU и спрашивают цену, action=price_check.
- Если есть артикул/SKU и спрашивают наличие/город, action=availability_check.
- Если сравнивают 2-3 бренда или товара, action=compare_products.
- Если есть площадь или просят посчитать бюджет, action=budget_estimate.
- Если просят подобрать/посоветовать под задачу, action=recommend_products.
- Если спрашивают акции/скидки/дешевле, action=discount_search.
- Если просто ищут товар без точного артикула, action=hybrid_product_search.

История:
{history_text or "(пусто)"}

Сообщение:
{message}

Черновой разбор правилами:
{json.dumps(plan_to_dict(fallback), ensure_ascii=False)}

Формат:
{{
  "action": "price_check",
  "query": "самостоятельный поисковый запрос",
  "sku": "",
  "city": "",
  "brands": [],
  "category": "",
  "surface": "",
  "room": "",
  "usage": "",
  "budget_max": null,
  "area_m2": null,
  "volume_l": null,
  "filters": {{}},
  "need_clarification": false,
  "clarification_question": "",
  "confidence": 0.0
}}
"""


def plan_to_dict(plan: ProductPlan) -> dict[str, Any]:
    return {
        "action": plan.action,
        "query": plan.query,
        "sku": plan.sku,
        "city": plan.city,
        "brands": plan.brands,
        "category": plan.category,
        "surface": plan.surface,
        "room": plan.room,
        "usage": plan.usage,
        "budget_max": str(plan.budget_max) if plan.budget_max is not None else None,
        "area_m2": str(plan.area_m2) if plan.area_m2 is not None else None,
        "volume_l": str(plan.volume_l) if plan.volume_l is not None else None,
        "filters": plan.filters,
        "need_clarification": plan.need_clarification,
        "clarification_question": plan.clarification_question,
        "confidence": plan.confidence,
        "source": plan.source,
    }


def normalize_action(value: Any, fallback: str) -> str:
    action = str(value or fallback).strip()
    return action if action in PRODUCT_ACTIONS else fallback


def coerce_plan(data: dict[str, Any], fallback: ProductPlan) -> ProductPlan:
    brands = data.get("brands") if isinstance(data.get("brands"), list) else fallback.brands
    filters = data.get("filters") if isinstance(data.get("filters"), dict) else {}
    return ProductPlan(
        action=normalize_action(data.get("action"), fallback.action),
        query=str(data.get("query") or fallback.query or "").strip(),
        sku=str(data.get("sku") or fallback.sku or "").strip(),
        city=str(data.get("city") or fallback.city or "").strip(),
        brands=[str(item).lower().strip() for item in brands if str(item).strip()][:3],
        category=str(data.get("category") or "").strip(),
        surface=str(data.get("surface") or "").strip(),
        room=str(data.get("room") or "").strip(),
        usage=str(data.get("usage") or "").strip(),
        budget_max=parse_decimal(data.get("budget_max")) or fallback.budget_max,
        area_m2=parse_decimal(data.get("area_m2")) or fallback.area_m2,
        volume_l=parse_decimal(data.get("volume_l")) or fallback.volume_l,
        filters=filters,
        need_clarification=bool(data.get("need_clarification")),
        clarification_question=str(data.get("clarification_question") or "").strip(),
        confidence=float(data.get("confidence") or 0.0),
        source="llm",
    )


def plan_product_message(
    message: str,
    classification: dict[str, Any] | None = None,
    history: list[dict[str, str]] | None = None,
    request_id: str = "-",
) -> ProductPlan:
    fallback = rule_plan(message, classification)
    if not llm.has_llm():
        return fallback
    try:
        content = llm.chat(
            [
                {"role": "system", "content": "Ты возвращаешь только JSON для product planner."},
                {"role": "user", "content": product_planner_prompt(message, history, fallback)},
            ],
            temperature=0,
            response_format={"type": "json_object"},
            request_id=request_id,
            purpose="product_planner",
        )
        return coerce_plan(llm.parse_json(content), fallback)
    except Exception as exc:
        logger.warning("request_id=%s stage=product_planner event=fallback error=%s", request_id, exc)
        return fallback


def build_ai_text_from_values(data: dict[str, Any]) -> str:
    stock = data.get("stock_by_city") if isinstance(data.get("stock_by_city"), dict) else {}
    characteristics = data.get("characteristics") if isinstance(data.get("characteristics"), dict) else {}
    category_path = data.get("category_path") if isinstance(data.get("category_path"), list) else []
    pieces = [
        f"Название: {data.get('name') or ''}",
        f"Бренд: {data.get('brand') or ''}",
        f"Артикул: {data.get('sku') or ''}",
        f"Категория: {data.get('category_name') or ''}",
        f"Путь категории: {' / '.join(str(item) for item in category_path)}",
        f"Верхняя категория: {data.get('top_category') or ''}",
        f"Подкатегория: {data.get('leaf_category') or ''}",
        f"Цена: {data.get('price_kzt') or ''} {data.get('currency') or 'KZT'}",
        f"Старая цена: {data.get('old_price_kzt') or ''}",
        f"Скидка: {data.get('discount_percent') or ''}",
        f"Наличие: {data.get('availability') or ''}",
        f"Остатки: {', '.join(f'{city}: {qty}' for city, qty in stock.items())}",
        f"Фасовка: {data.get('package_size') or ''}",
        f"Страна: {data.get('country') or ''}",
        f"Описание: {data.get('description') or ''}",
        f"Полное описание: {data.get('full_description') or ''}",
        f"Характеристики: {'; '.join(f'{key}: {value}' for key, value in characteristics.items())}",
    ]
    return "\n".join(piece for piece in pieces if piece.split(": ", 1)[-1].strip())


def build_ai_text(product: Product) -> str:
    return build_ai_text_from_values(
        {
            "name": product.name,
            "brand": product.brand,
            "sku": product.sku,
            "category_name": product.category_name,
            "category_path": product.category_path,
            "top_category": product.top_category,
            "leaf_category": product.leaf_category,
            "price_kzt": product.price_kzt,
            "old_price_kzt": product.old_price_kzt,
            "discount_percent": product.discount_percent,
            "currency": product.currency,
            "availability": product.availability,
            "stock_by_city": product.stock_by_city,
            "package_size": product.package_size,
            "country": product.country,
            "description": product.description,
            "full_description": product.full_description,
            "characteristics": product.characteristics,
        }
    )


def active_products() -> QuerySet[Product]:
    return Product.objects.filter(is_active=True)


def exact_sku_products(sku: str) -> QuerySet[Product]:
    if not sku:
        return Product.objects.none()
    return active_products().filter(Q(sku__iexact=sku) | Q(site_product_id__iexact=sku) | Q(product_key__iexact=sku))


def brand_q(brand: str) -> Q:
    query = Q()
    for alias in BRAND_ALIASES.get(brand, [brand]):
        normalized_alias = normalize_text(alias)
        query |= Q(brand__icontains=alias)
        query |= Q(name__icontains=alias)
        query |= Q(product_key__icontains=normalized_alias.replace(" ", "_"))
        query |= Q(normalized_text__icontains=normalized_alias)
        query |= Q(ai_text__icontains=alias)
    return query


def text_q(query_text: str) -> Q:
    query = Q()
    for word in normalize_text(query_text).split():
        if len(word) < 3:
            continue
        query &= (
            Q(name__icontains=word)
            | Q(category_name__icontains=word)
            | Q(top_category__icontains=word)
            | Q(leaf_category__icontains=word)
            | Q(normalized_text__icontains=word)
            | Q(ai_text__icontains=word)
        )
    return query


def ensure_product_search_indexes() -> None:
    with connection.cursor() as cursor:
        cursor.execute("create extension if not exists pg_trgm")
        cursor.execute("create extension if not exists vector")
        cursor.execute(
            "create index if not exists products_product_ai_text_trgm_idx "
            "on products_product using gin (ai_text gin_trgm_ops)"
        )


def product_card(product: Product) -> dict[str, Any]:
    return {
        "id": product.id,
        "name": product.name,
        "sku": product.sku,
        "brand": product.brand,
        "category": product.category_name,
        "top_category": product.top_category,
        "leaf_category": product.leaf_category,
        "price_kzt": str(product.price_kzt) if product.price_kzt is not None else None,
        "old_price_kzt": str(product.old_price_kzt) if product.old_price_kzt is not None else None,
        "discount_percent": str(product.discount_percent) if product.discount_percent is not None else None,
        "currency": product.currency or "KZT",
        "availability": product.availability,
        "stock_by_city": product.stock_by_city or {},
        "can_buy": product.can_buy,
        "package_size": product.package_size,
        "source_url": product.source_url,
        "description": product.description,
        "characteristics": product.characteristics or {},
    }


def product_score(product: Product, plan: ProductPlan) -> tuple[int, int]:
    normalized_name = normalize_text(product.name)
    normalized_sku = normalize_text(product.sku)
    haystack = normalize_text(" ".join([product.ai_text, product.normalized_text, product.name]))
    score = 0
    if plan.sku and normalize_text(plan.sku) == normalized_sku:
        score += 1000
    for brand in plan.brands:
        if normalize_text(brand) in haystack:
            score += 120
    for word in normalize_text(plan.query).split():
        if word in normalized_name:
            score += 50
        elif word in haystack:
            score += 15
    if product.can_buy:
        score += 10
    if product.price_kzt is not None:
        score += 5
    return score, -product.id


def lexical_product_search(plan: ProductPlan, limit: int = HYBRID_PREFETCH) -> list[Product]:
    if plan.sku:
        return list(exact_sku_products(plan.sku).order_by("id")[:limit])

    qs = active_products()
    filters = Q()
    for brand in plan.brands:
        filters |= brand_q(brand)
    if plan.query:
        text_filter = text_q(plan.query)
        filters = filters & text_filter if filters else text_filter
    if plan.category:
        filters &= Q(category_name__icontains=plan.category) | Q(top_category__icontains=plan.category) | Q(leaf_category__icontains=plan.category)
    if plan.budget_max is not None:
        qs = qs.filter(Q(price_kzt__isnull=True) | Q(price_kzt__lte=plan.budget_max))
    if plan.action == "discount_search":
        qs = qs.filter(Q(discount_percent__isnull=False) | Q(old_price_kzt__isnull=False))
    if not filters:
        qs = qs.none()
    else:
        qs = qs.filter(filters)
    rows = list(qs[:200])
    rows.sort(key=lambda item: product_score(item, plan), reverse=True)
    return rows[:limit]


def trigram_product_search(plan: ProductPlan, limit: int = HYBRID_PREFETCH) -> list[Product]:
    if not plan.query:
        return []
    normalized = normalize_text(" ".join([plan.query, " ".join(plan.brands), plan.category, plan.surface, plan.room, plan.usage]))
    if not normalized:
        return []
    like_pattern = f"%{normalized}%"
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                with scored as (
                    select id,
                           greatest(
                               similarity(normalized_text, %s),
                               similarity(lower(coalesce(ai_text, '')), %s),
                               similarity(lower(coalesce(name, '')), %s),
                               similarity(lower(coalesce(sku, '')), %s),
                               similarity(lower(coalesce(brand, '')), %s),
                               similarity(lower(coalesce(category_name, '')), %s),
                               case when normalized_text like %s then 0.95 else 0 end,
                               case when lower(coalesce(ai_text, '')) like %s then 0.80 else 0 end
                           ) as score
                    from products_product
                    where is_active = true
                )
                select id
                from scored
                where score >= %s
                order by score desc, id asc
                limit %s
                """,
                [
                    normalized,
                    normalized,
                    normalized,
                    normalized,
                    normalized,
                    normalized,
                    like_pattern,
                    like_pattern,
                    PRODUCT_TRGM_THRESHOLD,
                    limit,
                ],
            )
            ids = [row[0] for row in cursor.fetchall()]
    except Exception as exc:
        logger.info("stage=product_search event=trigram_failed error=%s", exc)
        return []
    by_id = Product.objects.in_bulk(ids)
    return [by_id[item_id] for item_id in ids if item_id in by_id]


def embed_query(query: str, request_id: str = "-") -> list[float]:
    ml_api_url = getattr(settings, "ML_API_URL", "")
    if not ml_api_url:
        return []
    try:
        response = httpx.post(
            f"{ml_api_url.rstrip('/')}/embed",
            json={"texts": [query]},
            headers={"X-Request-ID": request_id},
            timeout=30,
        )
        response.raise_for_status()
        vectors = response.json().get("vectors") or []
        return vectors[0] if vectors else []
    except Exception as exc:
        logger.info("request_id=%s stage=product_search event=embed_query_failed error=%s", request_id, exc)
        return []


def vector_literal(vector: list[float]) -> str:
    return "[" + ",".join(f"{float(value):.8f}" for value in vector) + "]"


def vector_product_search(plan: ProductPlan, request_id: str = "-", limit: int = HYBRID_PREFETCH) -> list[Product]:
    vector = embed_query(plan.query, request_id=request_id)
    if not vector:
        return []
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                select id
                from products_product
                where is_active = true and embedding is not null
                order by embedding <=> %s::vector
                limit %s
                """,
                [vector_literal(vector), limit],
            )
            ids = [row[0] for row in cursor.fetchall()]
    except Exception as exc:
        logger.info("request_id=%s stage=product_search event=vector_failed error=%s", request_id, exc)
        return []
    by_id = Product.objects.in_bulk(ids)
    return [by_id[item_id] for item_id in ids if item_id in by_id]


def rerank_products(query: str, products: list[Product], request_id: str = "-") -> list[Product]:
    ml_api_url = getattr(settings, "ML_API_URL", "")
    if not ml_api_url or not products:
        return products
    candidates = [
        {
            "id": product.id,
            "question": product.ai_text or build_ai_text(product),
        }
        for product in products
    ]
    try:
        response = httpx.post(
            f"{ml_api_url.rstrip('/')}/rerank",
            json={"query": query, "candidates": candidates},
            headers={"X-Request-ID": request_id},
            timeout=30,
        )
        response.raise_for_status()
        ordered_ids = [item["id"] for item in response.json().get("candidates", [])]
    except Exception as exc:
        logger.info("request_id=%s stage=product_search event=rerank_failed error=%s", request_id, exc)
        return products
    by_id = {product.id: product for product in products}
    return [by_id[item_id] for item_id in ordered_ids if item_id in by_id]


def hybrid_product_search(plan: ProductPlan, request_id: str = "-", limit: int = TOP_PRODUCTS) -> tuple[list[Product], dict[str, Any]]:
    started = perf_counter()
    ensure_product_search_indexes()
    exact = list(exact_sku_products(plan.sku).order_by("id")[:limit]) if plan.sku else []
    if exact:
        return exact, {"exact": len(exact), "lexical": 0, "trigram": 0, "vector": 0, "rerank": 0, "duration_ms": round((perf_counter() - started) * 1000)}
    if plan.sku and plan.action in {"exact_product_lookup", "price_check", "availability_check"}:
        return [], {"exact": 0, "lexical": 0, "trigram": 0, "vector": 0, "rerank": 0, "duration_ms": round((perf_counter() - started) * 1000)}

    lexical = lexical_product_search(plan)
    trigram = trigram_product_search(plan)
    vector = vector_product_search(plan, request_id=request_id)
    merged: list[Product] = []
    seen: set[int] = set()
    for product in [*lexical, *trigram, *vector]:
        if product.id in seen:
            continue
        seen.add(product.id)
        merged.append(product)
    merged.sort(key=lambda item: product_score(item, plan), reverse=True)
    reranked = rerank_products(plan.query, merged[:HYBRID_PREFETCH], request_id=request_id)
    result = reranked[:limit]
    return result, {
        "exact": 0,
        "lexical": len(lexical),
        "trigram": len(trigram),
        "vector": len(vector),
        "rerank": len(reranked),
        "duration_ms": round((perf_counter() - started) * 1000),
    }


def city_stock(product: Product, city: str = "") -> str:
    stock = product.stock_by_city or {}
    if city:
        return str(stock.get(city) or "")
    return ", ".join(f"{key}: {value}" for key, value in stock.items())


def availability_text(product: Product, city: str = "") -> str:
    stock = city_stock(product, city)
    if stock:
        return f"{city + ': ' if city else ''}{stock}"
    if product.availability == "in_stock" or product.can_buy:
        return "есть в наличии"
    if product.availability == "not_available" or product.can_buy is False:
        return "нет в наличии"
    return product.availability or "наличие не указано"


def product_line(product: Product, city: str = "", include_stock: bool = True) -> str:
    parts = [product.name]
    if product.sku:
        parts.append(f"арт. {product.sku}")
    if product.price_kzt is not None:
        parts.append(money(product.price_kzt, product.currency))
    if include_stock:
        parts.append(availability_text(product, city))
    return " - ".join(parts)


def answer_single_product(product: Product, plan: ProductPlan) -> str:
    lines = [product.name]
    if product.sku:
        lines.append(f"Артикул: {product.sku}")
    if product.category_name:
        lines.append(f"Категория: {product.category_name}")
    if product.price_kzt is not None:
        lines.append(f"Цена: {money(product.price_kzt, product.currency)}")
    if product.old_price_kzt:
        lines.append(f"Старая цена: {money(product.old_price_kzt, product.currency)}")
    if product.discount_percent:
        lines.append(f"Скидка: {product.discount_percent}%")
    lines.append(f"Наличие: {availability_text(product, plan.city)}")
    return "\n".join(lines)


def answer_product_list(products: list[Product], plan: ProductPlan) -> str:
    if not products:
        if plan.sku:
            return f"По артикулу {plan.sku} товар не найден."
        return "По товарам ничего не нашел. Попробуйте уточнить бренд, категорию или артикул."
    if len(products) == 1 or (plan.sku and plan.action in {"price_check", "availability_check", "exact_product_lookup"}):
        return answer_single_product(products[0], plan)
    lines = ["Нашел несколько подходящих товаров:"]
    for index, product in enumerate(products, start=1):
        lines.append(f"{index}. {product_line(product, plan.city)}")
    return "\n".join(lines)


def package_liters(product: Product) -> Decimal | None:
    candidates = [product.name, product.package_size, product.description, product.full_description]
    for value in candidates:
        match = VOLUME_RE.search(value or "")
        if match:
            return parse_decimal(match.group(1))
    return None


def coverage_from_product(product: Product) -> Decimal:
    blob = " ".join(
        [
            product.description or "",
            product.full_description or "",
            " ".join(f"{key} {value}" for key, value in (product.characteristics or {}).items()),
        ]
    )
    normalized = normalize_text(blob)
    match = re.search(r"(\d+(?:[,.]\d+)?)\s*(?:м2|м²|кв м)\s*(?:на|/)\s*(?:1\s*)?л", normalized, re.IGNORECASE)
    if match:
        value = parse_decimal(match.group(1))
        if value and value > 0:
            return value
    return DEFAULT_COVERAGE_M2_PER_LITER


def budget_candidate_allowed(product: Product, plan: ProductPlan) -> bool:
    haystack = normalize_text(" ".join([product.name, product.category_name, product.top_category, product.leaf_category]))
    normalized = normalize_text(" ".join([plan.query, plan.surface, plan.room, plan.usage]))
    if "грунт" in normalized:
        return "грунт" in haystack
    if "лак" in normalized:
        return "лак" in haystack
    if "штукатур" in normalized:
        return "штукатур" in haystack
    if "эмаль" in normalized:
        return "эмаль" in haystack
    if any(marker in normalized for marker in ["стен", "потол", "комнат", "ванн", "фасад", "детск"]):
        return "краска" in haystack and "пробник" not in haystack
    return "пробник" not in haystack


def tool_hybrid_product_search(plan: ProductPlan, request_id: str = "-") -> ToolResult:
    products, retrieval = hybrid_product_search(plan, request_id=request_id)
    return ToolResult(
        tool="hybrid_product_search",
        products=products,
        answer=answer_product_list(products, plan),
        facts={"products": [product_card(item) for item in products]},
        retrieval=retrieval,
    )


def tool_exact_product_lookup(plan: ProductPlan, request_id: str = "-") -> ToolResult:
    products, retrieval = hybrid_product_search(plan, request_id=request_id)
    return ToolResult(
        tool="exact_product_lookup",
        products=products,
        answer=answer_product_list(products, plan),
        facts={"products": [product_card(item) for item in products]},
        retrieval=retrieval,
    )


def tool_availability_check(plan: ProductPlan, request_id: str = "-") -> ToolResult:
    products, retrieval = hybrid_product_search(plan, request_id=request_id)
    return ToolResult(
        tool="availability_check",
        products=products,
        answer=answer_product_list(products, plan),
        facts={"products": [product_card(item) for item in products], "city": plan.city},
        retrieval=retrieval,
    )


def tool_price_check(plan: ProductPlan, request_id: str = "-") -> ToolResult:
    products, retrieval = hybrid_product_search(plan, request_id=request_id)
    return ToolResult(
        tool="price_check",
        products=products,
        answer=answer_product_list(products, plan),
        facts={"products": [product_card(item) for item in products]},
        retrieval=retrieval,
    )


def tool_discount_search(plan: ProductPlan, request_id: str = "-") -> ToolResult:
    products, retrieval = hybrid_product_search(plan, request_id=request_id)
    return ToolResult(
        tool="discount_search",
        products=products,
        answer=answer_product_list(products, plan),
        facts={"products": [product_card(item) for item in products]},
        retrieval=retrieval,
    )


def tool_compare_products(plan: ProductPlan, request_id: str = "-") -> ToolResult:
    if len(plan.brands) < 2:
        answer = "Напишите 2-3 бренда, которые нужно сравнить."
        return ToolResult(tool="compare_products", answer=answer, facts={"reason": "missing_brands"})
    context = plan.query.lower()
    for brand in plan.brands:
        for alias in BRAND_ALIASES.get(brand, [brand]):
            context = context.replace(alias.lower(), " ")
    for marker in [*INTENT_WORDS["compare"], "и", "или", "and", "or"]:
        context = context.replace(marker, " ")
    context = re.sub(r"[^0-9a-zа-я]+", " ", context, flags=re.IGNORECASE).strip()
    if not context:
        answer = "Уточните, по какой категории сравнить бренды: интерьерные краски, фасадные краски, лаки, грунтовки?"
        return ToolResult(tool="compare_products", answer=answer, facts={"reason": "missing_category"})

    lines = [f"Сравнение по запросу: {plan.query}"]
    selected: list[Product] = []
    for brand in plan.brands[:3]:
        qs = active_products().filter(brand_q(brand)).filter(text_q(plan.query)).filter(price_kzt__isnull=False)
        products = sorted(list(qs[:200]), key=lambda item: (item.price_kzt or Decimal("0"), item.id))
        available = [item for item in products if item.can_buy or item.availability == "in_stock"]
        if not products:
            lines.append(f"{brand.title()}: подходящих товаров не нашел.")
            continue
        prices = [item.price_kzt for item in products if item.price_kzt is not None]
        cheapest = products[0]
        selected.append(cheapest)
        lines.append(
            f"{brand.title()}: {money(min(prices))} - {money(max(prices))}, "
            f"в наличии: {len(available)}. Минимальный вариант: {product_line(cheapest, plan.city, include_stock=False)}"
        )
    return ToolResult(
        tool="compare_products",
        products=selected,
        answer="\n".join(lines),
        facts={"products": [product_card(item) for item in selected], "brands": plan.brands[:3]},
        retrieval={"brands": len(plan.brands[:3])},
    )


def tool_budget_estimate(plan: ProductPlan, request_id: str = "-") -> ToolResult:
    if plan.area_m2 is None:
        answer = "Напишите площадь в м², и я посчитаю примерный бюджет."
        return ToolResult(tool="budget_estimate", answer=answer, facts={"reason": "missing_area"})

    candidates, retrieval = hybrid_product_search(plan, request_id=request_id, limit=20)
    if not candidates and plan.brands:
        qs = active_products().filter(price_kzt__isnull=False)
        brand_filter = Q()
        for brand in plan.brands:
            brand_filter |= brand_q(brand)
        candidates = list(qs.filter(brand_filter)[:20])
    filtered = [item for item in candidates if budget_candidate_allowed(item, plan)]
    if filtered:
        candidates = filtered
    candidates = [item for item in candidates if item.price_kzt is not None and package_liters(item)]
    candidates.sort(key=lambda item: item.price_kzt or Decimal("0"))
    if not candidates:
        answer = "Не нашел подходящих товаров с ценой и фасовкой для расчета. Уточните категорию или бренд."
        return ToolResult(tool="budget_estimate", answer=answer, facts={"reason": "no_budget_candidates"}, retrieval=retrieval)

    options: list[tuple[Decimal, Product, Decimal, int]] = []
    seen: set[str] = set()
    for product in candidates:
        identity = normalize_text(product.name)
        if identity in seen:
            continue
        seen.add(identity)
        liters_per_pack = package_liters(product)
        coverage = coverage_from_product(product)
        if not liters_per_pack or coverage <= 0 or not product.price_kzt:
            continue
        liters_needed = (plan.area_m2 * DEFAULT_COATS / coverage).quantize(Decimal("0.1"))
        packs = int(math.ceil(float(liters_needed / liters_per_pack)))
        total = product.price_kzt * packs
        options.append((total, product, liters_needed, packs))

    options.sort(key=lambda item: (item[0], item[1].id))
    lines = [
        f"Ориентировочный расчет на {plan.area_m2} м²:",
        f"Беру {DEFAULT_COATS} слоя. Если в товаре не указан расход, считаю {DEFAULT_COVERAGE_M2_PER_LITER} м²/л.",
    ]
    products: list[Product] = []
    for total, product, liters_needed, packs in options[:3]:
        products.append(product)
        lines.append(f"- {product.name}: нужно около {liters_needed} л, упаковок: {packs}, бюджет: {money(total, product.currency)}")
    return ToolResult(
        tool="budget_estimate",
        products=products,
        answer="\n".join(lines),
        facts={"products": [product_card(item) for item in products], "area_m2": str(plan.area_m2)},
        retrieval=retrieval,
    )


def tool_recommend_products(plan: ProductPlan, request_id: str = "-") -> ToolResult:
    products, retrieval = hybrid_product_search(plan, request_id=request_id)
    if not products:
        return ToolResult(
            tool="recommend_products",
            answer="Не нашел подходящих вариантов. Уточните поверхность, помещение, бюджет или бренд.",
            facts={"reason": "not_found"},
            retrieval=retrieval,
        )
    lines = ["Я бы посмотрел эти варианты:"]
    for index, product in enumerate(products[:3], start=1):
        reason = product.category_name or product.top_category or "подходит под запрос"
        lines.append(f"{index}. {product_line(product, plan.city)}. Почему: {reason}.")
    return ToolResult(
        tool="recommend_products",
        products=products[:3],
        answer="\n".join(lines),
        facts={"products": [product_card(item) for item in products[:3]]},
        retrieval=retrieval,
    )


PRODUCT_TOOLS: dict[str, Callable[[ProductPlan, str], ToolResult]] = {
    "exact_product_lookup": tool_exact_product_lookup,
    "hybrid_product_search": tool_hybrid_product_search,
    "availability_check": tool_availability_check,
    "price_check": tool_price_check,
    "discount_search": tool_discount_search,
    "compare_products": tool_compare_products,
    "budget_estimate": tool_budget_estimate,
    "recommend_products": tool_recommend_products,
}


def answer_product_message(
    message: str,
    classification: dict[str, Any] | None = None,
    history: list[dict[str, str]] | None = None,
    request_id: str = "-",
) -> dict[str, Any]:
    plan = plan_product_message(message, classification, history, request_id=request_id)
    if plan.need_clarification and plan.clarification_question:
        result = ToolResult(tool="clarification", answer=plan.clarification_question, facts={"reason": "planner_clarification"})
    else:
        handler = PRODUCT_TOOLS.get(plan.action, tool_hybrid_product_search)
        result = handler(plan, request_id)

    return {
        "answer": result.answer,
        "metadata": {
            "route": "product_agent",
            "intent": plan.action,
            "action": plan.action,
            "tool": result.tool,
            "query": plan.query or message,
            "sku": plan.sku,
            "city": plan.city,
            "brands": plan.brands,
            "product_ids": [item.id for item in result.products],
            "retrieval": result.retrieval,
        },
        "debug": {
            "product_plan": plan_to_dict(plan),
            "tool": result.tool,
            "facts": result.facts,
            "retrieval": result.retrieval,
        },
    }


# Backward-compatible aliases used by older tests and callers.
ProductQuery = ProductPlan
parse_product_query = rule_plan
find_products = lambda plan, limit=TOP_PRODUCTS: hybrid_product_search(plan, limit=limit)[0]
compare_brands = lambda plan: tool_compare_products(plan).answer
budget_estimate = lambda plan: tool_budget_estimate(plan).answer
