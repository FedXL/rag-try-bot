import re

from django.db import migrations


def normalize_text(value: str) -> str:
    value = (value or "").lower().replace("ё", "е")
    value = re.sub(r"[^0-9a-zа-я]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def class_kind(kind: str) -> str:
    if kind == "article":
        return "source"
    return kind or "source"


def migrate_content(apps, schema_editor):
    OldClassifierClass = apps.get_model("core", "ClassifierClass")
    OldQAItem = apps.get_model("core", "QAItem")
    OldArticle = apps.get_model("core", "Article")
    OldArticleChunk = apps.get_model("core", "ArticleChunk")
    OldQuickPhrase = apps.get_model("core", "QuickPhrase")
    NewClassifierClass = apps.get_model("content", "ClassifierClass")
    Source = apps.get_model("content", "Source")
    NewQuickPhrase = apps.get_model("content", "QuickPhrase")

    class_by_old_id = {}
    class_by_slug = {}
    for row in OldClassifierClass.objects.order_by("priority", "slug"):
        new_class, _ = NewClassifierClass.objects.update_or_create(
            slug=row.slug,
            defaults={
                "title": row.title,
                "description": row.description,
                "kind": class_kind(row.kind),
                "priority": row.priority,
                "is_active": row.is_active,
                "metadata": row.metadata,
            },
        )
        class_by_old_id[row.id] = new_class
        class_by_slug[row.slug] = new_class

    for item in OldQAItem.objects.select_related("classifier_class").order_by("source_number").iterator():
        classifier_class = class_by_old_id.get(item.classifier_class_id)
        source_key = f"catalog-qaitem-{item.source_number}"
        title = (item.question_ru or f"Source #{item.source_number}")[:500]
        body = f"{item.question_ru or ''}\n\n{item.answer_ru or ''}".strip()
        Source.objects.update_or_create(
            source_key=source_key,
            defaults={
                "source_number": item.source_number,
                "classifier_class": classifier_class,
                "title": title,
                "body": body,
                "source_url": "",
                "normalized_text": normalize_text(f"{title} {body}"),
                "metadata": {"legacy_qaitem_id": item.id, "source_number": item.source_number},
                "is_active": True,
            },
        )

    for article in OldArticle.objects.select_related("classifier_class").order_by("id").iterator():
        metadata = article.metadata or {}
        source_key = article.article_key
        source_number = None
        if metadata.get("legacy_qaitem_id"):
            source_number = metadata.get("source_number")
            source_key = f"catalog-qaitem-{source_number}" if source_number else article.article_key
        body = article.body
        if not body:
            chunks = OldArticleChunk.objects.filter(article_id=article.id, is_active=True).order_by("chunk_index")
            body = "\n\n".join(chunk.chunk_text for chunk in chunks)
        Source.objects.update_or_create(
            source_key=source_key,
            defaults={
                "source_number": source_number,
                "classifier_class": class_by_old_id.get(article.classifier_class_id),
                "title": article.title[:500] or source_key,
                "body": body,
                "source_url": article.source_url,
                "normalized_text": article.normalized_text or normalize_text(f"{article.title} {body}"),
                "metadata": metadata,
                "is_active": article.is_active,
            },
        )

    for chunk in OldArticleChunk.objects.filter(article__isnull=True).order_by("article_key", "chunk_index").iterator():
        source_key = chunk.article_key
        Source.objects.get_or_create(
            source_key=source_key,
            defaults={
                "source_number": None,
                "classifier_class": class_by_slug.get(chunk.section),
                "title": chunk.title[:500] or source_key,
                "body": chunk.chunk_text,
                "source_url": chunk.source_url,
                "normalized_text": chunk.normalized_text or normalize_text(f"{chunk.title} {chunk.chunk_text}"),
                "metadata": chunk.metadata or {},
                "is_active": chunk.is_active,
            },
        )

    for quick in OldQuickPhrase.objects.select_related("article").order_by("priority", "id").iterator():
        source_key = quick.article.article_key if quick.article_id else quick.article_key
        article_metadata = quick.article.metadata if quick.article_id and quick.article.metadata else {}
        if article_metadata.get("legacy_qaitem_id") and article_metadata.get("source_number"):
            source_key = f"catalog-qaitem-{article_metadata['source_number']}"
        source = Source.objects.filter(source_key=source_key).first()
        if not source:
            continue
        NewQuickPhrase.objects.update_or_create(
            source=source,
            normalized_phrase=quick.normalized_phrase,
            defaults={
                "phrase": quick.phrase,
                "priority": quick.priority,
                "is_active": quick.is_active,
                "metadata": quick.metadata or {},
            },
        )


class Migration(migrations.Migration):
    dependencies = [
        ("content", "0001_initial"),
        ("core", "0006_llmrequestlog"),
    ]

    operations = [
        migrations.RunPython(migrate_content, migrations.RunPython.noop),
        migrations.DeleteModel(name="QuickPhrase"),
        migrations.DeleteModel(name="ArticleChunk"),
        migrations.DeleteModel(name="Product"),
        migrations.DeleteModel(name="QAItem"),
        migrations.DeleteModel(name="Article"),
        migrations.DeleteModel(name="SearchThresholdProfile"),
        migrations.DeleteModel(name="EmbeddingRun"),
        migrations.DeleteModel(name="ClassifierClass"),
    ]
