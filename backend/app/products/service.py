from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_CEILING
from typing import Any

from django.db.models import Q, QuerySet

from app.core.text import normalize_text

from .models import Product


TOP_PRODUCTS = 5
DEFAULT_COATS = 2
DEFAULT_COVERAGE_M2_PER_LITER = Decimal("10")

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

SKU_RE = re.compile(r"\b[a-zа-я]*\d{3,}[a-zа-я0-9/_-]*\b", re.IGNORECASE)
AREA_RE = re.compile(r"(\d+(?:[,.]\d+)?)\s*(?:м2|м²|кв\.?\s*м|квадрат(?:ов|а|ные|ный)?)", re.IGNORECASE)
VOLUME_RE = re.compile(r"(\d+(?:[,.]\d+)?)\s*(?:л|литр(?:а|ов)?)\b", re.IGNORECASE)

INTENT_WORDS = {
    "price": ["цена", "стоимость", "сколько стоит", "почем", "по чем"],
    "stock": ["наличие", "есть ли", "в наличии", "остаток", "остатки", "сколько осталось"],
    "discount": ["скидка", "скидки", "акция", "акции", "акционный", "дешевле", "дешево", "бюджетный"],
    "compare": ["сравни", "сравнить", "сравнение", "что дешевле", "какой дешевле", "между"],
    "budget": ["бюджет", "рассчитай", "расчитать", "посчитай", "посчитать", "сколько выйдет", "на сколько хватит"],
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
class ProductQuery:
    raw: str
    normalized: str
    intent: str
    sku: str = ""
    city: str = ""
    brands: list[str] = field(default_factory=list)
    area_m2: Decimal | None = None
    volume_l: Decimal | None = None
    query_text: str = ""


def money(value: Any, currency: str = "KZT") -> str:
    if value in {None, ""}:
        return "-"
    amount = Decimal(str(value)).quantize(Decimal("1"), rounding=ROUND_CEILING)
    return f"{amount:,.0f}".replace(",", " ") + f" {currency or 'KZT'}"


def parse_decimal(value: str) -> Decimal | None:
    try:
        return Decimal(value.replace(",", "."))
    except Exception:
        return None


def detect_brands(normalized: str) -> list[str]:
    brands: list[str] = []
    for canonical, aliases in BRAND_ALIASES.items():
        if any(normalize_text(alias) in normalized for alias in aliases):
            brands.append(canonical)
    return brands


def detect_intent(normalized: str, brands: list[str], area_m2: Decimal | None) -> str:
    if area_m2 is not None or any(marker in normalized for marker in INTENT_WORDS["budget"]):
        return "budget_estimate"
    if len(brands) >= 2 or any(marker in normalized for marker in INTENT_WORDS["compare"]):
        return "product_compare"
    if any(marker in normalized for marker in INTENT_WORDS["discount"]):
        return "product_discount"
    if any(marker in normalized for marker in INTENT_WORDS["stock"]):
        return "product_stock"
    if any(marker in normalized for marker in INTENT_WORDS["price"]):
        return "product_price"
    if normalized.startswith(("какие есть", "что есть", "покажи", "найди")):
        return "product_category"
    return "product_lookup"


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


def parse_product_query(message: str, classification: dict[str, Any] | None = None) -> ProductQuery:
    normalized = normalize_text(message)
    slots = classification.get("slots", {}) if isinstance(classification, dict) else {}
    sku = str(slots.get("article_or_sku") or "").strip()
    if not sku:
        match = SKU_RE.search(normalized)
        sku = match.group(0).upper() if match else ""

    city = ""
    for marker, canonical in CITY_ALIASES.items():
        if marker in normalized:
            city = canonical
            break

    area_m2 = None
    area_match = AREA_RE.search(message)
    if area_match:
        area_m2 = parse_decimal(area_match.group(1))

    volume_l = None
    volume_match = VOLUME_RE.search(message)
    if volume_match:
        volume_l = parse_decimal(volume_match.group(1))

    brands = detect_brands(normalized)
    if slots.get("brand") and str(slots["brand"]).lower() not in brands:
        brands.append(str(slots["brand"]).lower())

    intent = str(classification.get("intent") or "") if isinstance(classification, dict) else ""
    standard_intent = detect_intent(normalized, brands, area_m2)
    if intent not in {
        "product_lookup",
        "product_price",
        "product_stock",
        "product_category",
        "product_discount",
        "product_compare",
        "budget_estimate",
        "product_recommendation",
    }:
        intent = standard_intent
    elif standard_intent in {"product_compare", "budget_estimate", "product_discount"}:
        intent = standard_intent
    elif standard_intent in {"product_price", "product_stock"}:
        intent = standard_intent

    return ProductQuery(
        raw=message,
        normalized=normalized,
        intent=intent,
        sku=sku,
        city=city,
        brands=brands[:3],
        area_m2=area_m2,
        volume_l=volume_l,
        query_text=clean_query_text(normalized, brands, sku, city),
    )


def brand_q(brand: str) -> Q:
    query = Q()
    for alias in BRAND_ALIASES.get(brand, [brand]):
        query |= Q(brand__icontains=alias)
        query |= Q(name__icontains=alias)
        query |= Q(product_key__icontains=normalize_text(alias).replace(" ", "_"))
        query |= Q(normalized_text__icontains=normalize_text(alias))
    return query


def text_q(query_text: str) -> Q:
    query = Q()
    for word in query_text.split():
        if len(word) < 3:
            continue
        query &= (
            Q(name__icontains=word)
            | Q(category_name__icontains=word)
            | Q(top_category__icontains=word)
            | Q(leaf_category__icontains=word)
            | Q(normalized_text__icontains=word)
        )
    return query


def active_products() -> QuerySet[Product]:
    return Product.objects.filter(is_active=True)


def exact_sku_products(sku: str) -> QuerySet[Product]:
    if not sku:
        return Product.objects.none()
    return active_products().filter(Q(sku__iexact=sku) | Q(site_product_id__iexact=sku) | Q(product_key__iexact=sku))


def product_score(product: Product, pq: ProductQuery) -> tuple[int, int]:
    normalized_name = normalize_text(product.name)
    normalized_sku = normalize_text(product.sku)
    score = 0
    if pq.sku and normalize_text(pq.sku) == normalized_sku:
        score += 1000
    for brand in pq.brands:
        if normalize_text(brand) in product.normalized_text or normalize_text(brand) in normalized_name:
            score += 120
    for word in pq.query_text.split():
        if word in normalized_name:
            score += 50
        elif word in product.normalized_text:
            score += 15
    if product.can_buy:
        score += 10
    if product.price_kzt is not None:
        score += 5
    return score, product.id


def find_products(pq: ProductQuery, limit: int = TOP_PRODUCTS) -> list[Product]:
    if pq.sku:
        exact = list(exact_sku_products(pq.sku).order_by("id")[:limit])
        if exact:
            return exact
        return []

    qs = active_products()
    filters = Q()
    for brand in pq.brands:
        filters |= brand_q(brand)
    if pq.query_text:
        text_filter = text_q(pq.query_text)
        filters = filters & text_filter if filters else text_filter
    if pq.intent == "product_discount":
        qs = qs.filter(Q(discount_percent__isnull=False) | Q(old_price_kzt__isnull=False))
    if filters:
        qs = qs.filter(filters)
    else:
        qs = qs.none()
    rows = list(qs[:200])
    rows.sort(key=lambda item: product_score(item, pq), reverse=True)
    return rows[:limit]


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


def answer_single_product(product: Product, pq: ProductQuery) -> str:
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
    lines.append(f"Наличие: {availability_text(product, pq.city)}")
    return "\n".join(lines)


def answer_product_list(products: list[Product], pq: ProductQuery) -> str:
    if not products:
        if pq.sku:
            return f"По артикулу {pq.sku} товар не найден."
        return "По товарам ничего не нашел. Попробуйте уточнить бренд, категорию или артикул."
    if len(products) == 1 or (pq.sku and pq.intent in {"product_price", "product_stock"}):
        return answer_single_product(products[0], pq)
    lines = ["Нашел несколько подходящих товаров:"]
    for index, product in enumerate(products, start=1):
        lines.append(f"{index}. {product_line(product, pq.city)}")
    return "\n".join(lines)


def compare_brands(pq: ProductQuery) -> str:
    if len(pq.brands) < 2:
        return "Напишите 2-3 бренда, которые нужно сравнить."
    if not pq.query_text:
        return "Уточните, по какой категории сравнить бренды: интерьерные краски, фасадные краски, лаки, грунтовки?"

    lines = [f"Сравнение по запросу: {pq.query_text}"]
    for brand in pq.brands[:3]:
        qs = active_products().filter(brand_q(brand)).filter(text_q(pq.query_text)).filter(price_kzt__isnull=False)
        products = sorted(list(qs[:200]), key=lambda item: (item.price_kzt or Decimal("0"), item.id))
        available = [item for item in products if item.can_buy or item.availability == "in_stock"]
        if not products:
            lines.append(f"{brand.title()}: подходящих товаров не нашел.")
            continue
        prices = [item.price_kzt for item in products if item.price_kzt is not None]
        cheapest = products[0]
        lines.append(
            f"{brand.title()}: {money(min(prices))} - {money(max(prices))}, "
            f"в наличии: {len(available)}. Минимальный вариант: {product_line(cheapest, pq.city, include_stock=False)}"
        )
    return "\n".join(lines)


def package_liters(product: Product) -> Decimal | None:
    candidates = [product.name, product.package_size, product.description, product.full_description]
    for value in candidates:
        match = VOLUME_RE.search(value or "")
        if match:
            return parse_decimal(match.group(1))
    return None


def budget_candidate_allowed(product: Product, pq: ProductQuery) -> bool:
    haystack = normalize_text(" ".join([product.name, product.category_name, product.top_category, product.leaf_category]))
    if "грунт" in pq.normalized:
        return "грунт" in haystack
    if "лак" in pq.normalized:
        return "лак" in haystack
    if "штукатур" in pq.normalized:
        return "штукатур" in haystack
    if "эмаль" in pq.normalized:
        return "эмаль" in haystack
    if any(marker in pq.normalized for marker in ["стен", "потол", "комнат", "ванн", "фасад", "детск"]):
        return "краска" in haystack and "пробник" not in haystack
    return "пробник" not in haystack


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


def budget_estimate(pq: ProductQuery) -> str:
    if pq.area_m2 is None:
        return "Напишите площадь в м², и я посчитаю примерный бюджет."

    candidates = find_products(pq, limit=20)
    if not candidates and pq.brands:
        qs = active_products().filter(price_kzt__isnull=False)
        brand_filter = Q()
        for brand in pq.brands:
            brand_filter |= brand_q(brand)
        candidates = list(qs.filter(brand_filter)[:20])
    filtered = [item for item in candidates if budget_candidate_allowed(item, pq)]
    if filtered:
        candidates = filtered
    candidates = [item for item in candidates if item.price_kzt is not None and package_liters(item)]
    candidates.sort(key=lambda item: item.price_kzt or Decimal("0"))
    if not candidates:
        return "Не нашел подходящих товаров с ценой и фасовкой для расчета. Уточните категорию или бренд."

    options: list[tuple[Decimal, Product, Decimal, int]] = []
    seen: set[tuple[str, str]] = set()
    for product in candidates:
        identity = normalize_text(product.name)
        if identity in seen:
            continue
        seen.add(identity)
        liters_per_pack = package_liters(product)
        coverage = coverage_from_product(product)
        if not liters_per_pack or coverage <= 0 or not product.price_kzt:
            continue
        liters_needed = (pq.area_m2 * DEFAULT_COATS / coverage).quantize(Decimal("0.1"))
        packs = int(math.ceil(float(liters_needed / liters_per_pack)))
        total = product.price_kzt * packs
        options.append((total, product, liters_needed, packs))

    options.sort(key=lambda item: (item[0], item[1].id))
    if not options:
        return "Не нашел подходящих товаров с ценой и фасовкой для расчета. Уточните категорию или бренд."

    lines = [
        f"Ориентировочный расчет на {pq.area_m2} м²:",
        f"Беру {DEFAULT_COATS} слоя. Если в товаре не указан расход, считаю {DEFAULT_COVERAGE_M2_PER_LITER} м²/л.",
    ]
    for total, product, liters_needed, packs in options[:3]:
        lines.append(
            f"- {product.name}: нужно около {liters_needed} л, упаковок: {packs}, бюджет: {money(total, product.currency)}"
        )
    return "\n".join(lines)


def answer_product_message(message: str, classification: dict[str, Any] | None = None) -> dict[str, Any]:
    pq = parse_product_query(message, classification)
    if pq.intent == "product_compare":
        answer = compare_brands(pq)
        products: list[Product] = []
    elif pq.intent == "budget_estimate":
        answer = budget_estimate(pq)
        products = []
    else:
        products = find_products(pq)
        answer = answer_product_list(products, pq)

    return {
        "answer": answer,
        "metadata": {
            "route": "product",
            "intent": pq.intent,
            "query": pq.query_text or pq.raw,
            "sku": pq.sku,
            "city": pq.city,
            "brands": pq.brands,
            "product_ids": [item.id for item in products],
        },
        "debug": {
            "product_query": {
                "intent": pq.intent,
                "sku": pq.sku,
                "city": pq.city,
                "brands": pq.brands,
                "area_m2": str(pq.area_m2) if pq.area_m2 is not None else None,
                "volume_l": str(pq.volume_l) if pq.volume_l is not None else None,
                "query_text": pq.query_text,
            }
        },
    }
