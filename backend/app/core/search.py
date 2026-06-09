import logging
import re
from time import perf_counter
from typing import Any

import httpx
from celery.result import AsyncResult
from django.conf import settings
from django.db import connection
from django.db.models import Count, Q

from .models import EmbeddingRun, QAItem, SearchThresholdProfile

logger = logging.getLogger(__name__)

TOP_K = 5
RETRIEVER_TOP_K = 20
STOPWORDS = {
    "это",
    "как",
    "что",
    "где",
    "когда",
    "какой",
    "какая",
    "какие",
    "почему",
    "можно",
    "нужно",
    "если",
    "для",
}

GUIDE_SOURCE_NUMBER_MIN = 199000


def is_guide_or_service(item: dict[str, Any]) -> bool:
    return int(item.get("number") or 0) >= GUIDE_SOURCE_NUMBER_MIN


def scope_boost(item: dict[str, Any], classification: dict[str, Any] | None) -> float:
    if not classification:
        return 0.0
    scope = classification.get("search_scope")
    intent = classification.get("intent")
    if scope in {"guides", "service"}:
        return 2.0 if is_guide_or_service(item) else -0.35
    if scope == "products":
        return 0.5 if not is_guide_or_service(item) else -0.25
    if intent == "product_recommendation":
        return 0.2 if not is_guide_or_service(item) else 0.15
    return 0.0


def ensure_profile() -> SearchThresholdProfile:
    profile, _ = SearchThresholdProfile.objects.get_or_create(name="default", defaults={"active": True})
    if not SearchThresholdProfile.objects.filter(active=True).exists():
        profile.active = True
        profile.save(update_fields=["active"])
    return SearchThresholdProfile.objects.filter(active=True).order_by("id").first() or profile


def ensure_pg() -> None:
    with connection.cursor() as cursor:
        cursor.execute("create extension if not exists vector")
        cursor.execute("create extension if not exists pg_trgm")
        cursor.execute("create index if not exists core_qaitem_question_ru_trgm_idx on core_qaitem using gin (question_ru gin_trgm_ops)")
        cursor.execute("""
            create table if not exists core_qaembedding (
                id bigserial primary key,
                qa_item_id bigint not null unique references core_qaitem(id) on delete cascade,
                embedding vector not null,
                created_at timestamptz not null default now()
            )
        """)


def vector_literal(vector: list[float]) -> str:
    return "[" + ",".join(f"{value:.8f}" for value in vector) + "]"


def ml_post(path: str, payload: dict[str, Any], request_id: str = "-") -> dict[str, Any]:
    started = perf_counter()
    logger.info(
        "request_id=%s stage=django->ml-api event=request path=%s payload_keys=%s",
        request_id,
        path,
        ",".join(sorted(payload.keys())),
    )
    with httpx.Client(timeout=settings.ML_API_TIMEOUT) as client:
        response = client.post(f"{settings.ML_API_URL}{path}", json=payload, headers={"X-Request-ID": request_id})
        response.raise_for_status()
        logger.info(
            "request_id=%s stage=ml-api->django event=response path=%s status=%s duration_ms=%s",
            request_id,
            path,
            response.status_code,
            round((perf_counter() - started) * 1000),
        )
        return response.json()


def embed_texts(texts: list[str], request_id: str = "-") -> list[list[float]]:
    logger.info("request_id=%s stage=search event=embed_texts count=%s", request_id, len(texts))
    return ml_post("/embed", {"texts": texts}, request_id=request_id)["vectors"]


def rerank(query: str, candidates: list[dict[str, Any]], request_id: str = "-") -> list[dict[str, Any]]:
    if not candidates:
        logger.info("request_id=%s stage=search event=rerank_skipped candidates=0", request_id)
        return []
    logger.info("request_id=%s stage=search event=rerank_start candidates=%s", request_id, len(candidates))
    return ml_post("/rerank", {"query": query, "candidates": candidates}, request_id=request_id)["candidates"]


def tokenize(text: str) -> set[str]:
    return {token for token in re.findall(r"[a-zа-яё0-9]{3,}", text.lower()) if token not in STOPWORDS}


def overlap(query: str, candidate: str) -> float:
    q = tokenize(query)
    if not q:
        return 0.0
    return len(q & tokenize(candidate)) / len(q)


def lexical_search(phrase: str, request_id: str = "-") -> list[dict[str, Any]]:
    started = perf_counter()
    ensure_pg()
    with connection.cursor() as cursor:
        cursor.execute(
            """
            select id, source_number, question_ru, answer_ru, similarity(question_ru, %s) as score
            from core_qaitem
            order by score desc, source_number asc
            limit %s
            """,
            [phrase, RETRIEVER_TOP_K],
        )
        cols = [col[0] for col in cursor.description]
        rows = [dict(zip(cols, row)) for row in cursor.fetchall()]
    result = [
        {
            "id": row["id"],
            "number": row["source_number"],
            "question": row["question_ru"],
            "answer": row["answer_ru"],
            "score": float(row["score"] or 0),
            "source": "lexical",
        }
        for row in rows
    ]
    logger.info(
        "request_id=%s stage=search event=lexical_done candidates=%s duration_ms=%s",
        request_id,
        len(result),
        round((perf_counter() - started) * 1000),
    )
    return result


def vector_search(phrase: str, request_id: str = "-") -> list[dict[str, Any]]:
    started = perf_counter()
    ensure_pg()
    if not EmbeddingRun.objects.filter(engine="pgvector", status="ready").exists():
        logger.info("request_id=%s stage=search event=vector_skipped reason=index_not_ready", request_id)
        return []
    vector = embed_texts([phrase], request_id=request_id)[0]
    with connection.cursor() as cursor:
        cursor.execute(
            """
            select q.id, q.source_number, q.question_ru, q.answer_ru,
                   1 - (e.embedding <=> %s::vector) as score
            from core_qaembedding e
            join core_qaitem q on q.id = e.qa_item_id
            order by e.embedding <=> %s::vector
            limit %s
            """,
            [vector_literal(vector), vector_literal(vector), RETRIEVER_TOP_K],
        )
        cols = [col[0] for col in cursor.description]
        rows = [dict(zip(cols, row)) for row in cursor.fetchall()]
    result = [
        {
            "id": row["id"],
            "number": row["source_number"],
            "question": row["question_ru"],
            "answer": row["answer_ru"],
            "score": float(row["score"] or 0),
            "source": "vector",
        }
        for row in rows
    ]
    logger.info(
        "request_id=%s stage=search event=vector_done candidates=%s duration_ms=%s",
        request_id,
        len(result),
        round((perf_counter() - started) * 1000),
    )
    return result


def merge(
    phrase: str,
    groups: list[list[dict[str, Any]]],
    request_id: str = "-",
    classification: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    merged: dict[int, dict[str, Any]] = {}
    for group in groups:
        for rank, item in enumerate(group, start=1):
            row = merged.setdefault(item["id"], {**item, "retrievers": {}, "rrf": 0.0})
            row["retrievers"][item["source"]] = {"rank": rank, "score": item["score"]}
            row["rrf"] += 1 / (60 + rank)
            row["lexical_signal"] = max(float(row.get("score", 0)), overlap(phrase, row.get("question", "")))
    for row in merged.values():
        row["domain_boost"] = scope_boost(row, classification)
    result = sorted(merged.values(), key=lambda x: (x["rrf"] + x.get("domain_boost", 0)), reverse=True)
    logger.info("request_id=%s stage=search event=merge_done candidates=%s", request_id, len(result))
    return result


def search(phrase: str, request_id: str = "-", classification: dict[str, Any] | None = None) -> dict[str, Any]:
    started = perf_counter()
    logger.info(
        "request_id=%s stage=search event=start query_len=%s intent=%s scope=%s",
        request_id,
        len(phrase),
        (classification or {}).get("intent"),
        (classification or {}).get("search_scope"),
    )
    profile = ensure_profile()
    lexical = lexical_search(phrase, request_id=request_id)
    vector = vector_search(phrase, request_id=request_id)
    candidates = merge(phrase, [vector, lexical], request_id=request_id, classification=classification)
    ranked = rerank(phrase, candidates[:20], request_id=request_id) if candidates else []
    for item in ranked:
        item["domain_boost"] = scope_boost(item, classification)
        item["final_score"] = float(item.get("reranker_score", item.get("score", 0)) or 0) + float(item.get("domain_boost", 0))
    ranked = sorted(ranked, key=lambda item: item.get("final_score", 0), reverse=True)
    top = ranked[0] if ranked else None
    score = float(top.get("final_score", top.get("reranker_score", top.get("score", 0)))) if top else 0.0
    lexical_signal = float(top.get("lexical_signal", 0)) if top else 0.0
    decision = "FOUND" if top and (score >= profile.found_threshold or lexical_signal >= profile.min_lexical_score) else "NOT_FOUND"
    logger.info(
        "request_id=%s stage=search event=done decision=%s lexical=%s vector=%s ranked=%s top_id=%s score=%.4f lexical_signal=%.4f duration_ms=%s",
        request_id,
        decision,
        len(lexical),
        len(vector),
        len(ranked),
        top.get("id") if top else None,
        score,
        lexical_signal,
        round((perf_counter() - started) * 1000),
    )
    return {
        "message": phrase,
        "decision": decision,
        "top_candidate": top,
        "candidates": ranked[:TOP_K],
        "retriever_breakdown": {"vector": len(vector), "lexical": len(lexical)},
    }


def prepare_async() -> AsyncResult:
    from .tasks import prepare_embeddings_task

    return prepare_embeddings_task.delay()


def health() -> dict[str, Any]:
    ensure_pg()
    aggregate = QAItem.objects.aggregate(total_questions=Count("id"), total_answers=Count("id", filter=~Q(answer_ru="")))
    run = EmbeddingRun.objects.filter(engine="pgvector").first()
    return {"ok": True, **aggregate, "embedding_status": run.status if run else "not_started", "embedded": run.embedded if run else 0}
