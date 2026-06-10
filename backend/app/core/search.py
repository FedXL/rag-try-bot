import logging
from time import perf_counter
from typing import Any

from django.db import connection
from django.db.models import Count, Q

from app.content.models import ClassifierClass, QuickPhrase, Source
from app.products.models import Product

from .text import normalize_text

logger = logging.getLogger(__name__)

TOP_K = 5
SOURCE_CLASS_LIMIT = 3
QUICK_PHRASE_TRGM_THRESHOLD = 0.45
PRODUCT_TRGM_THRESHOLD = 0.08

_pg_ready = False


def ensure_pg() -> None:
    global _pg_ready
    if _pg_ready:
        return
    with connection.cursor() as cursor:
        cursor.execute("create extension if not exists pg_trgm")
        cursor.execute(
            "create index if not exists content_quickphrase_normalized_trgm_idx "
            "on content_quickphrase using gin (normalized_phrase gin_trgm_ops)"
        )
        cursor.execute(
            "create index if not exists products_product_normalized_trgm_idx "
            "on products_product using gin (normalized_text gin_trgm_ops)"
        )
    _pg_ready = True


def normalized_class(classification: dict[str, Any] | None) -> str:
    value = str((classification or {}).get("class_slug") or (classification or {}).get("section") or (classification or {}).get("search_scope") or "mixed")
    return "product" if value in {"catalog", "brands"} else value


def classifier_skips_search(classification: dict[str, Any] | None) -> bool:
    if not classification:
        return False
    return classification.get("need_search") is False or normalized_class(classification) == "none"


def placeholders(values: list[Any]) -> str:
    return ", ".join(["%s"] * len(values))


def source_class_candidates(classification: dict[str, Any] | None) -> list[str]:
    slug = normalized_class(classification)
    if slug == "product":
        return []
    if slug in {"", "none", "mixed"}:
        return list(
            ClassifierClass.objects.filter(is_active=True, kind__in=["source", "system"])
            .exclude(slug__in=["product", "none"])
            .order_by("slug")
            .values_list("slug", flat=True)
        )
    return [slug, "mixed"]


def removed_embedding_status() -> dict[str, Any]:
    return {
        "status": "removed",
        "embedded": 0,
        "total": 0,
        "error": "",
        "article": {"engine": "removed", "status": "removed", "embedded": 0, "total": 0, "error": ""},
        "product": {"engine": "removed", "status": "removed", "embedded": 0, "total": 0, "error": ""},
    }


def source_candidate(
    source: Source,
    score: float,
    quick: QuickPhrase | None = None,
    source_label: str = "class_source",
    retriever_name: str = "class_sources",
    rank: int = 1,
) -> dict[str, Any]:
    payload = {
        "id": source.id,
        "content_type": "source",
        "source_id": source.id,
        "class_slug": source.classifier_class.slug if source.classifier_class_id else "mixed",
        "section": source.classifier_class.slug if source.classifier_class_id else "mixed",
        "source_url": source.source_url,
        "question": f"source:{source.id}",
        "answer": source.body,
        "score": score,
        "source": source_label,
        "lexical_signal": score,
        "retrievers": {retriever_name: {"rank": rank, "score": score}},
    }
    if quick:
        payload["quick_phrase_id"] = quick.id
        payload["quick_phrase"] = quick.phrase
    return payload


def class_source_search(phrase: str, classification: dict[str, Any] | None, request_id: str = "-") -> list[dict[str, Any]]:
    started = perf_counter()
    class_slug = normalized_class(classification)
    if class_slug in {"", "none", "product", "catalog", "brands"}:
        logger.info("request_id=%s stage=search event=class_sources_skipped class_slug=%s", request_id, class_slug)
        return []

    sources = list(
        Source.objects.filter(is_active=True, classifier_class__is_active=True, classifier_class__slug=class_slug)
        .select_related("classifier_class")
        .order_by("id")[:SOURCE_CLASS_LIMIT]
    )
    candidates = [
        source_candidate(source, 1.0, source_label="class_source", retriever_name="class_sources", rank=rank)
        for rank, source in enumerate(sources, start=1)
    ]
    logger.info(
        "request_id=%s stage=search event=class_sources_done class_slug=%s candidates=%s duration_ms=%s",
        request_id,
        class_slug,
        len(candidates),
        round((perf_counter() - started) * 1000),
    )
    return candidates


def product_candidate(row: dict[str, Any], score: float) -> dict[str, Any]:
    answer = row.get("description") or row.get("full_description") or row.get("name") or ""
    price = row.get("price_kzt")
    details: list[str] = []
    if row.get("brand"):
        details.append(f"Бренд: {row['brand']}")
    if row.get("sku"):
        details.append(f"Артикул: {row['sku']}")
    if row.get("category_name"):
        details.append(f"Категория: {row['category_name']}")
    if price not in {None, ""}:
        details.append(f"Цена: {price} {row.get('currency') or 'KZT'}")
    if row.get("old_price_kzt"):
        details.append(f"Старая цена: {row['old_price_kzt']} {row.get('currency') or 'KZT'}")
    if row.get("discount_percent"):
        details.append(f"Скидка: {row['discount_percent']}%")
    if row.get("availability"):
        details.append(f"Наличие: {row['availability']}")
    stock = row.get("stock_by_city") or {}
    if isinstance(stock, dict) and stock:
        details.append("Остатки: " + ", ".join(f"{city}: {qty}" for city, qty in stock.items()))
    if details:
        answer = f"{answer}\n" + "\n".join(details)
        answer = answer.strip()
    return {
        "id": row["id"],
        "content_type": "product",
        "product_id": row["id"],
        "product_key": row["product_key"],
        "article_key": row["product_key"],
        "class_slug": "product",
        "section": "product",
        "source_url": row.get("source_url") or "",
        "title": row.get("name") or "",
        "question": row.get("name") or "",
        "answer": answer,
        "score": score,
        "source": "product_lexical",
        "lexical_signal": score,
        "retrievers": {"product_lexical": {"rank": 1, "score": score}},
        "sku": row.get("sku") or "",
        "brand": row.get("brand") or "",
        "category_name": row.get("category_name") or "",
        "category_path": row.get("category_path") or [],
        "top_category": row.get("top_category") or "",
        "leaf_category": row.get("leaf_category") or "",
        "price_kzt": str(price) if price not in {None, ""} else None,
        "old_price_kzt": str(row.get("old_price_kzt")) if row.get("old_price_kzt") not in {None, ""} else None,
        "discount_percent": str(row.get("discount_percent")) if row.get("discount_percent") not in {None, ""} else None,
        "currency": row.get("currency") or "KZT",
        "availability": row.get("availability") or "",
        "stock_by_city": row.get("stock_by_city") or {},
        "can_buy": row.get("can_buy"),
        "max_quantity": str(row.get("max_quantity")) if row.get("max_quantity") not in {None, ""} else None,
        "package_size": row.get("package_size") or "",
        "country": row.get("country") or "",
        "image_urls": row.get("image_urls") or [],
    }


def quick_phrase_search(phrase: str, classification: dict[str, Any] | None, request_id: str = "-") -> dict[str, Any] | None:
    started = perf_counter()
    ensure_pg()
    normalized = normalize_text(phrase)
    classes = source_class_candidates(classification)
    if not classes:
        logger.info("request_id=%s stage=search event=quick_phrase_skipped reason=product_class", request_id)
        return None

    quick = (
        QuickPhrase.objects.filter(is_active=True, normalized_phrase=normalized, source__is_active=True, source__classifier_class__slug__in=classes)
        .select_related("source", "source__classifier_class")
        .order_by("priority", "id")
        .first()
    )
    exact = bool(quick)

    if not quick:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                select qp.id, similarity(qp.normalized_phrase, %s) as score
                from content_quickphrase qp
                join content_source s on qp.source_id = s.id
                join content_classifierclass cc on s.classifier_class_id = cc.id
                where qp.is_active = true
                  and s.is_active = true
                  and cc.slug in ({placeholders(classes)})
                  and similarity(qp.normalized_phrase, %s) >= %s
                order by score desc, qp.priority asc, qp.id asc
                limit 1
                """,
                [normalized, *classes, normalized, QUICK_PHRASE_TRGM_THRESHOLD],
            )
            row = cursor.fetchone()
            if row:
                quick = QuickPhrase.objects.filter(id=row[0]).select_related("source", "source__classifier_class").first()
                if quick:
                    quick._search_score = float(row[1] or 0)  # type: ignore[attr-defined]

    if not quick:
        logger.info(
            "request_id=%s stage=search event=quick_phrase_miss classes=%s duration_ms=%s",
            request_id,
            ",".join(classes),
            round((perf_counter() - started) * 1000),
        )
        return None

    score = 1.0 if exact else float(getattr(quick, "_search_score", 0.0))
    candidate = source_candidate(quick.source, score, quick, source_label="quick_phrase", retriever_name="quick_phrase")
    logger.info(
        "request_id=%s stage=search event=quick_phrase_hit quick_phrase_id=%s source_id=%s exact=%s score=%.4f duration_ms=%s",
        request_id,
        quick.id,
        quick.source_id,
        exact,
        score,
        round((perf_counter() - started) * 1000),
    )
    return {
        "quick_phrase": {
            "id": quick.id,
            "phrase": quick.phrase,
            "class_slug": quick.source.classifier_class.slug if quick.source.classifier_class_id else "mixed",
            "section": quick.source.classifier_class.slug if quick.source.classifier_class_id else "mixed",
            "score": score,
            "exact": exact,
        },
        "candidates": [candidate],
    }


def product_search(phrase: str, request_id: str = "-") -> list[dict[str, Any]]:
    started = perf_counter()
    ensure_pg()
    normalized = normalize_text(phrase)
    like_pattern = f"%{normalized}%"
    with connection.cursor() as cursor:
        cursor.execute(
            """
            with scored as (
            select id, product_key, name, sku, brand, category_name, category_path, top_category,
                   leaf_category, price_kzt, old_price_kzt, discount_percent, currency, availability,
                   stock_by_city, can_buy, max_quantity, package_size, country, image_urls,
                   source_url, description, full_description,
                   greatest(
                       similarity(normalized_text, %s),
                       similarity(lower(coalesce(product_key, '')), %s),
                       similarity(lower(coalesce(name, '')), %s),
                       similarity(lower(coalesce(sku, '')), %s),
                       similarity(lower(coalesce(brand, '')), %s),
                       similarity(lower(coalesce(category_name, '')), %s),
                       similarity(lower(coalesce(top_category, '')), %s),
                       similarity(lower(coalesce(leaf_category, '')), %s),
                       case when normalized_text like %s then 0.95 else 0 end,
                       case when lower(coalesce(sku, '')) like %s then 1.0 else 0 end
                   ) as score
            from products_product
            where is_active = true
            )
            select *
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
                normalized,
                normalized,
                like_pattern,
                like_pattern,
                PRODUCT_TRGM_THRESHOLD,
                TOP_K,
            ],
        )
        cols = [col[0] for col in cursor.description]
        rows = [dict(zip(cols, row)) for row in cursor.fetchall()]
    result = [product_candidate(row, float(row["score"] or 0)) for row in rows]
    for rank, item in enumerate(result, start=1):
        item["retrievers"] = {"product_lexical": {"rank": rank, "score": item["score"]}}
    logger.info("request_id=%s stage=search event=product_search_done candidates=%s duration_ms=%s", request_id, len(result), round((perf_counter() - started) * 1000))
    return result


def search(phrase: str, request_id: str = "-", classification: dict[str, Any] | None = None) -> dict[str, Any]:
    started = perf_counter()
    class_slug = normalized_class(classification)
    logger.info("request_id=%s stage=search event=start query_len=%s intent=%s class_slug=%s", request_id, len(phrase), (classification or {}).get("intent"), class_slug)

    if classifier_skips_search(classification):
        logger.info("request_id=%s stage=search event=skipped reason=classifier class_slug=%s duration_ms=%s", request_id, class_slug, round((perf_counter() - started) * 1000))
        return {
            "message": phrase,
            "decision": "SKIPPED",
            "route": "classifier",
            "quick_phrase": None,
            "top_candidate": None,
            "candidates": [],
            "retriever_breakdown": {"quick_phrase": 0, "product_lexical": 0, "class_sources": 0},
            "embedding_status": removed_embedding_status(),
            "classification": classification,
        }

    if class_slug == "product":
        candidates = product_search(phrase, request_id=request_id)
        decision = "FOUND" if candidates else "NOT_FOUND"
        route = "product"
        quick = None
    else:
        quick = None
        candidates = class_source_search(phrase, classification, request_id=request_id)
        decision = "FOUND" if candidates else "NOT_FOUND"
        route = "class_sources"

    logger.info(
        "request_id=%s stage=search event=done route=%s decision=%s candidates=%s duration_ms=%s",
        request_id,
        route,
        decision,
        len(candidates),
        round((perf_counter() - started) * 1000),
    )
    return {
        "message": phrase,
        "decision": decision,
        "route": route,
        "quick_phrase": quick["quick_phrase"] if quick else None,
        "top_candidate": candidates[0] if candidates else None,
        "candidates": candidates[:TOP_K],
        "retriever_breakdown": {
            "quick_phrase": len(candidates) if route == "quick_phrase" else 0,
            "product_lexical": len(candidates) if route == "product" else 0,
            "class_sources": len(candidates) if route == "class_sources" else 0,
        },
        "embedding_status": removed_embedding_status(),
        "classification": classification,
    }


def prepare_async() -> None:
    return None


def health() -> dict[str, Any]:
    ensure_pg()
    quick = QuickPhrase.objects.aggregate(total_quick_phrases=Count("id"), active_quick_phrases=Count("id", filter=Q(is_active=True)))
    sources = Source.objects.aggregate(total_sources=Count("id"), active_sources=Count("id", filter=Q(is_active=True)))
    products = Product.objects.aggregate(total_products=Count("id"), active_products=Count("id", filter=Q(is_active=True)))
    classes = ClassifierClass.objects.aggregate(total_classes=Count("id"), active_classes=Count("id", filter=Q(is_active=True)))
    status = removed_embedding_status()
    return {
        "ok": True,
        **classes,
        **sources,
        **products,
        **quick,
        "total_questions": quick["total_quick_phrases"],
        "total_answers": sources["total_sources"] + products["total_products"],
        "embedding_status": status["status"],
        "embedded": status["embedded"],
        "embedding": status,
    }
