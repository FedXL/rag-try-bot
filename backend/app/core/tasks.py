import logging

from celery import shared_task
from django.db import connection

from .models import EmbeddingRun, QAItem

logger = logging.getLogger(__name__)


def vector_literal(vector: list[float]) -> str:
    return "[" + ",".join(f"{value:.8f}" for value in vector) + "]"


@shared_task(name="app.core.tasks.prepare_embeddings_task")
def prepare_embeddings_task() -> dict:
    from .search import embed_texts, ensure_pg

    ensure_pg()
    items = list(QAItem.objects.order_by("source_number"))
    run, _ = EmbeddingRun.objects.get_or_create(engine="pgvector")
    run.status = "building"
    run.total = len(items)
    run.embedded = 0
    run.error = ""
    run.save()
    batch_size = 32
    logger.info("request_id=- stage=celery event=prepare_embeddings_start total=%s batch_size=%s", len(items), batch_size)
    try:
        with connection.cursor() as cursor:
            cursor.execute("truncate table core_qaembedding")
        logger.info("request_id=- stage=celery event=embeddings_table_truncated")
        for start in range(0, len(items), batch_size):
            batch = items[start:start + batch_size]
            logger.info(
                "request_id=- stage=celery event=embedding_batch_start batch_start=%s batch_size=%s",
                start,
                len(batch),
            )
            vectors = embed_texts([item.question_ru for item in batch], request_id="celery-index")
            with connection.cursor() as cursor:
                for item, vector in zip(batch, vectors):
                    cursor.execute(
                        """
                        insert into core_qaembedding (qa_item_id, embedding)
                        values (%s, %s::vector)
                        on conflict (qa_item_id) do update set embedding = excluded.embedding, created_at = now()
                        """,
                        [item.id, vector_literal(vector)],
                    )
            run.embedded += len(batch)
            run.save(update_fields=["embedded", "updated_at"])
            logger.info("request_id=- stage=celery event=embedding_batch_done embedded=%s total=%s", run.embedded, run.total)
        run.status = "ready"
        run.save(update_fields=["status", "updated_at"])
        logger.info("request_id=- stage=celery event=prepare_embeddings_done total=%s embedded=%s", run.total, run.embedded)
        return {"status": "ready", "total": run.total, "embedded": run.embedded}
    except Exception as exc:
        run.status = "error"
        run.error = str(exc)
        run.save(update_fields=["status", "error", "updated_at"])
        logger.exception("request_id=- stage=celery event=prepare_embeddings_failed error=%s", exc)
        raise
