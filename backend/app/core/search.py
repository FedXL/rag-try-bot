import logging
from time import perf_counter
from typing import Any

import httpx
from celery.result import AsyncResult
from django.conf import settings
from django.db import connection
from django.db.models import Count, Q

from .models import ArticleChunk, EmbeddingRun, QuickPhrase, SearchThresholdProfile
from .text import normalize_text

logger = logging.getLogger(__name__)

TOP_K = 5
RETRIEVER_TOP_K = 20
QUICK_PHRASE_TRGM_THRESHOLD = 0.45
ARTICLE_TRGM_THRESHOLD = 0.08
ARTICLE_EMBEDDING_ENGINE = "articlechunk_pgvector"
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
        cursor.execute("alter table core_articlechunk add column if not exists embedding vector")
        cursor.execute(
            "create index if not exists core_articlechunk_normalized_trgm_idx "
            "on core_articlechunk using gin (normalized_text gin_trgm_ops)"
        )
        cursor.execute(
            "create index if not exists core_quickphrase_normalized_trgm_idx "
            "on core_quickphrase using gin (normalized_phrase gin_trgm_ops)"
        )


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


def section_candidates(classification: dict[str, Any] | None) -> list[str]:
    section = str((classification or {}).get("section") or (classification or {}).get("search_scope") or "mixed")
    if section == "brands":
        section = "catalog"
    if section in {"", "none", "mixed"}:
        return ["catalog", "help", "about", "inspiration", "color_selection", "partners", "glossary", "contacts", "news_articles", "mixed"]
    return [section, "mixed"]


def placeholders(values: list[Any]) -> str:
    return ", ".join(["%s"] * len(values))


def chunk_to_candidate(chunk: ArticleChunk | dict[str, Any], source: str, score: float = 1.0, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    if isinstance(chunk, ArticleChunk):
        payload = {
            "id": chunk.id,
            "article_key": chunk.article_key,
            "section": chunk.section,
            "source_url": chunk.source_url,
            "title": chunk.title,
            "chunk_index": chunk.chunk_index,
            "question": chunk.title,
            "answer": chunk.chunk_text,
            "score": score,
            "source": source,
        }
    else:
        payload = {
            "id": chunk["id"],
            "article_key": chunk["article_key"],
            "section": chunk["section"],
            "source_url": chunk.get("source_url") or "",
            "title": chunk.get("title") or "",
            "chunk_index": chunk.get("chunk_index") or 0,
            "question": chunk.get("title") or "",
            "answer": chunk.get("chunk_text") or "",
            "score": score,
            "source": source,
        }
    if extra:
        payload.update(extra)
    return payload


def quick_phrase_search(phrase: str, classification: dict[str, Any] | None, request_id: str = "-") -> dict[str, Any] | None:
    started = perf_counter()
    ensure_pg()
    normalized = normalize_text(phrase)
    sections = section_candidates(classification)

    quick = (
        QuickPhrase.objects.filter(is_active=True, normalized_phrase=normalized, section__in=sections)
        .order_by("priority", "id")
        .first()
    )
    exact = bool(quick)

    if not quick:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                select id, phrase, normalized_phrase, section, article_key, target_chunk_index, priority,
                       similarity(normalized_phrase, %s) as score
                from core_quickphrase
                where is_active = true
                  and section in ({placeholders(sections)})
                  and similarity(normalized_phrase, %s) >= %s
                order by score desc, priority asc, id asc
                limit 1
                """,
                [normalized, *sections, normalized, QUICK_PHRASE_TRGM_THRESHOLD],
            )
            row = cursor.fetchone()
            if row:
                cols = [col[0] for col in cursor.description]
                data = dict(zip(cols, row))
                quick = QuickPhrase(
                    id=data["id"],
                    phrase=data["phrase"],
                    normalized_phrase=data["normalized_phrase"],
                    section=data["section"],
                    article_key=data["article_key"],
                    target_chunk_index=data["target_chunk_index"],
                    priority=data["priority"],
                )
                quick._search_score = float(data["score"] or 0)  # type: ignore[attr-defined]

    if not quick:
        logger.info(
            "request_id=%s stage=search event=quick_phrase_miss sections=%s duration_ms=%s",
            request_id,
            ",".join(sections),
            round((perf_counter() - started) * 1000),
        )
        return None

    chunks_qs = ArticleChunk.objects.filter(is_active=True, article_key=quick.article_key)
    if quick.target_chunk_index is not None:
        chunks_qs = chunks_qs.filter(chunk_index=quick.target_chunk_index)
    chunks = list(chunks_qs.order_by("chunk_index")[:TOP_K])
    score = 1.0 if exact else float(getattr(quick, "_search_score", 0.0))
    candidates = [
        chunk_to_candidate(
            chunk,
            source="quick_phrase",
            score=score,
            extra={
                "quick_phrase_id": quick.id,
                "quick_phrase": quick.phrase,
                "retrievers": {"quick_phrase": {"rank": index, "score": score}},
                "lexical_signal": score,
            },
        )
        for index, chunk in enumerate(chunks, start=1)
    ]
    logger.info(
        "request_id=%s stage=search event=quick_phrase_hit quick_phrase_id=%s article_key=%s exact=%s score=%.4f chunks=%s duration_ms=%s",
        request_id,
        quick.id,
        quick.article_key,
        exact,
        score,
        len(candidates),
        round((perf_counter() - started) * 1000),
    )
    return {
        "quick_phrase": {
            "id": quick.id,
            "phrase": quick.phrase,
            "section": quick.section,
            "article_key": quick.article_key,
            "target_chunk_index": quick.target_chunk_index,
            "score": score,
            "exact": exact,
        },
        "candidates": candidates,
    }


def article_lexical_search(phrase: str, classification: dict[str, Any] | None, request_id: str = "-") -> list[dict[str, Any]]:
    started = perf_counter()
    ensure_pg()
    normalized = normalize_text(phrase)
    sections = section_candidates(classification)
    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            select id, article_key, section, source_url, title, chunk_index, chunk_text,
                   similarity(normalized_text, %s) as score
            from core_articlechunk
            where is_active = true
              and section in ({placeholders(sections)})
              and similarity(normalized_text, %s) >= %s
            order by score desc, id asc
            limit %s
            """,
            [normalized, *sections, normalized, ARTICLE_TRGM_THRESHOLD, RETRIEVER_TOP_K],
        )
        cols = [col[0] for col in cursor.description]
        rows = [dict(zip(cols, row)) for row in cursor.fetchall()]
    result = [chunk_to_candidate(row, "lexical", float(row["score"] or 0)) for row in rows]
    logger.info(
        "request_id=%s stage=search event=article_lexical_done sections=%s candidates=%s duration_ms=%s",
        request_id,
        ",".join(sections),
        len(result),
        round((perf_counter() - started) * 1000),
    )
    return result


def article_vector_search(phrase: str, classification: dict[str, Any] | None, request_id: str = "-") -> list[dict[str, Any]]:
    started = perf_counter()
    ensure_pg()
    run = EmbeddingRun.objects.filter(engine=ARTICLE_EMBEDDING_ENGINE).first()
    if not run or run.status != "ready":
        logger.info(
            "request_id=%s stage=search event=vector_skipped reason=index_not_ready status=%s embedded=%s",
            request_id,
            run.status if run else "not_started",
            run.embedded if run else 0,
        )
        return []

    vector = embed_texts([phrase], request_id=request_id)[0]
    vector_text = vector_literal(vector)
    sections = section_candidates(classification)
    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            select id, article_key, section, source_url, title, chunk_index, chunk_text,
                   1 - (embedding <=> %s::vector) as score
            from core_articlechunk
            where is_active = true
              and embedding is not null
              and section in ({placeholders(sections)})
            order by embedding <=> %s::vector
            limit %s
            """,
            [vector_text, *sections, vector_text, RETRIEVER_TOP_K],
        )
        cols = [col[0] for col in cursor.description]
        rows = [dict(zip(cols, row)) for row in cursor.fetchall()]
    result = [chunk_to_candidate(row, "vector", float(row["score"] or 0)) for row in rows]
    logger.info(
        "request_id=%s stage=search event=article_vector_done sections=%s candidates=%s duration_ms=%s",
        request_id,
        ",".join(sections),
        len(result),
        round((perf_counter() - started) * 1000),
    )
    return result


def token_overlap(query: str, candidate: str) -> float:
    q = {token for token in normalize_text(query).split() if len(token) >= 3 and token not in STOPWORDS}
    if not q:
        return 0.0
    c = {token for token in normalize_text(candidate).split() if len(token) >= 3 and token not in STOPWORDS}
    return len(q & c) / len(q)


def merge(phrase: str, groups: list[list[dict[str, Any]]], request_id: str = "-") -> list[dict[str, Any]]:
    merged: dict[int, dict[str, Any]] = {}
    for group in groups:
        for rank, item in enumerate(group, start=1):
            row = merged.setdefault(item["id"], {**item, "retrievers": {}, "rrf": 0.0})
            row["retrievers"][item["source"]] = {"rank": rank, "score": item["score"]}
            row["rrf"] += 1 / (60 + rank)
            row["lexical_signal"] = max(
                float(row.get("lexical_signal", 0) or 0),
                float(item.get("score", 0) or 0),
                token_overlap(phrase, f"{row.get('question', '')} {row.get('answer', '')}"),
            )
    result = sorted(merged.values(), key=lambda item: (item.get("rrf", 0), item.get("lexical_signal", 0)), reverse=True)
    logger.info("request_id=%s stage=search event=merge_done candidates=%s", request_id, len(result))
    return result


def embedding_status_payload() -> dict[str, Any]:
    run = EmbeddingRun.objects.filter(engine=ARTICLE_EMBEDDING_ENGINE).first()
    return {
        "engine": ARTICLE_EMBEDDING_ENGINE,
        "status": run.status if run else "not_started",
        "embedded": run.embedded if run else 0,
        "total": run.total if run else 0,
        "error": run.error if run else "",
    }


def search(phrase: str, request_id: str = "-", classification: dict[str, Any] | None = None) -> dict[str, Any]:
    started = perf_counter()
    section = (classification or {}).get("section") or (classification or {}).get("search_scope") or "mixed"
    logger.info(
        "request_id=%s stage=search event=start query_len=%s intent=%s section=%s",
        request_id,
        len(phrase),
        (classification or {}).get("intent"),
        section,
    )
    profile = ensure_profile()

    quick = quick_phrase_search(phrase, classification, request_id=request_id)
    if quick and quick["candidates"]:
        candidates = quick["candidates"]
        logger.info(
            "request_id=%s stage=search event=done route=quick_phrase decision=FOUND candidates=%s duration_ms=%s",
            request_id,
            len(candidates),
            round((perf_counter() - started) * 1000),
        )
        return {
            "message": phrase,
            "decision": "FOUND",
            "route": "quick_phrase",
            "quick_phrase": quick["quick_phrase"],
            "top_candidate": candidates[0],
            "candidates": candidates[:TOP_K],
            "retriever_breakdown": {"quick_phrase": len(candidates), "lexical": 0, "vector": 0},
            "embedding_status": embedding_status_payload(),
        }

    lexical = article_lexical_search(phrase, classification, request_id=request_id)
    vector = article_vector_search(phrase, classification, request_id=request_id)
    candidates = merge(phrase, [vector, lexical], request_id=request_id)
    ranked = rerank(phrase, candidates[:20], request_id=request_id) if candidates else []
    for item in ranked:
        item["final_score"] = float(item.get("reranker_score", item.get("score", 0)) or 0)
    ranked = sorted(ranked, key=lambda item: item.get("final_score", 0), reverse=True)

    top = ranked[0] if ranked else None
    score = float(top.get("final_score", top.get("reranker_score", top.get("score", 0)))) if top else 0.0
    lexical_signal = float(top.get("lexical_signal", 0)) if top else 0.0
    decision = "FOUND" if top and (score >= profile.found_threshold or lexical_signal >= profile.min_lexical_score) else "NOT_FOUND"
    logger.info(
        "request_id=%s stage=search event=done route=article_chunks decision=%s lexical=%s vector=%s ranked=%s top_id=%s score=%.4f lexical_signal=%.4f duration_ms=%s",
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
        "route": "article_chunks",
        "quick_phrase": None,
        "top_candidate": top,
        "candidates": ranked[:TOP_K],
        "retriever_breakdown": {"quick_phrase": 0, "lexical": len(lexical), "vector": len(vector)},
        "embedding_status": embedding_status_payload(),
    }


def prepare_async() -> AsyncResult:
    from .tasks import prepare_embeddings_task

    return prepare_embeddings_task.apply_async(queue="default")


def health() -> dict[str, Any]:
    ensure_pg()
    chunks = ArticleChunk.objects.aggregate(
        total_chunks=Count("id"),
        active_chunks=Count("id", filter=Q(is_active=True)),
    )
    quick = QuickPhrase.objects.aggregate(
        total_quick_phrases=Count("id"),
        active_quick_phrases=Count("id", filter=Q(is_active=True)),
    )
    status = embedding_status_payload()
    return {
        "ok": True,
        **chunks,
        **quick,
        "total_questions": quick["total_quick_phrases"],
        "total_answers": chunks["total_chunks"],
        "embedding_status": status["status"],
        "embedded": status["embedded"],
        "embedding": status,
    }
