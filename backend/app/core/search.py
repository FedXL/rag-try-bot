import re
from typing import Any

import httpx
from celery.result import AsyncResult
from django.conf import settings
from django.db import connection
from django.db.models import Count, Q

from .models import EmbeddingRun, QAItem, SearchThresholdProfile

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


def ml_post(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    with httpx.Client(timeout=settings.ML_API_TIMEOUT) as client:
        response = client.post(f"{settings.ML_API_URL}{path}", json=payload)
        response.raise_for_status()
        return response.json()


def embed_texts(texts: list[str]) -> list[list[float]]:
    return ml_post("/embed", {"texts": texts})["vectors"]


def rerank(query: str, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not candidates:
        return []
    return ml_post("/rerank", {"query": query, "candidates": candidates})["candidates"]


def tokenize(text: str) -> set[str]:
    return {token for token in re.findall(r"[a-zа-яё0-9]{3,}", text.lower()) if token not in STOPWORDS}


def overlap(query: str, candidate: str) -> float:
    q = tokenize(query)
    if not q:
        return 0.0
    return len(q & tokenize(candidate)) / len(q)


def lexical_search(phrase: str) -> list[dict[str, Any]]:
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
    return [
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


def vector_search(phrase: str) -> list[dict[str, Any]]:
    ensure_pg()
    if not EmbeddingRun.objects.filter(engine="pgvector", status="ready").exists():
        return []
    vector = embed_texts([phrase])[0]
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
    return [
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


def merge(phrase: str, groups: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    merged: dict[int, dict[str, Any]] = {}
    for group in groups:
        for rank, item in enumerate(group, start=1):
            row = merged.setdefault(item["id"], {**item, "retrievers": {}, "rrf": 0.0})
            row["retrievers"][item["source"]] = {"rank": rank, "score": item["score"]}
            row["rrf"] += 1 / (60 + rank)
            row["lexical_signal"] = max(float(row.get("score", 0)), overlap(phrase, row.get("question", "")))
    return sorted(merged.values(), key=lambda x: x["rrf"], reverse=True)


def search(phrase: str) -> dict[str, Any]:
    profile = ensure_profile()
    lexical = lexical_search(phrase)
    vector = vector_search(phrase)
    candidates = merge(phrase, [vector, lexical])
    ranked = rerank(phrase, candidates[:20]) if candidates else []
    top = ranked[0] if ranked else None
    score = float(top.get("reranker_score", top.get("score", 0))) if top else 0.0
    lexical_signal = float(top.get("lexical_signal", 0)) if top else 0.0
    decision = "FOUND" if top and (score >= profile.found_threshold or lexical_signal >= profile.min_lexical_score) else "NOT_FOUND"
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
