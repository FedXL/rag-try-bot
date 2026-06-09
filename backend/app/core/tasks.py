import logging

from celery import shared_task
from django.db import connection

from .models import ArticleChunk, EmbeddingRun
from .search import ARTICLE_EMBEDDING_ENGINE

logger = logging.getLogger(__name__)


def vector_literal(vector: list[float]) -> str:
    return "[" + ",".join(f"{value:.8f}" for value in vector) + "]"


@shared_task(name="app.core.tasks.prepare_embeddings_task")
def prepare_embeddings_task() -> dict:
    from .search import embed_texts, ensure_pg

    ensure_pg()
    chunks = list(ArticleChunk.objects.filter(is_active=True).order_by("section", "article_key", "chunk_index"))
    run, _ = EmbeddingRun.objects.get_or_create(engine=ARTICLE_EMBEDDING_ENGINE)
    run.status = "building"
    run.total = len(chunks)
    run.embedded = 0
    run.error = ""
    run.save()
    batch_size = 32
    logger.info("request_id=- stage=celery event=prepare_article_embeddings_start total=%s batch_size=%s", len(chunks), batch_size)
    try:
        with connection.cursor() as cursor:
            cursor.execute("update core_articlechunk set embedding = null")
        logger.info("request_id=- stage=celery event=article_embeddings_cleared")

        for start in range(0, len(chunks), batch_size):
            batch = chunks[start:start + batch_size]
            logger.info(
                "request_id=- stage=celery event=article_embedding_batch_start batch_start=%s batch_size=%s",
                start,
                len(batch),
            )
            texts = [f"{chunk.title}\n\n{chunk.chunk_text}".strip() for chunk in batch]
            vectors = embed_texts(texts, request_id="celery-index")
            with connection.cursor() as cursor:
                for chunk, vector in zip(batch, vectors):
                    cursor.execute(
                        "update core_articlechunk set embedding = %s::vector where id = %s",
                        [vector_literal(vector), chunk.id],
                    )
            run.embedded += len(batch)
            run.save(update_fields=["embedded", "updated_at"])
            logger.info("request_id=- stage=celery event=article_embedding_batch_done embedded=%s total=%s", run.embedded, run.total)

        run.status = "ready"
        run.save(update_fields=["status", "updated_at"])
        logger.info("request_id=- stage=celery event=prepare_article_embeddings_done total=%s embedded=%s", run.total, run.embedded)
        return {"status": "ready", "total": run.total, "embedded": run.embedded}
    except Exception as exc:
        run.status = "error"
        run.error = str(exc)
        run.save(update_fields=["status", "error", "updated_at"])
        logger.exception("request_id=- stage=celery event=prepare_article_embeddings_failed error=%s", exc)
        raise
