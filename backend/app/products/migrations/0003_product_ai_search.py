from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("products", "0002_widen_product_text_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="product",
            name="ai_text",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.RunSQL("create extension if not exists vector", reverse_sql=migrations.RunSQL.noop),
        migrations.RunSQL("create extension if not exists pg_trgm", reverse_sql=migrations.RunSQL.noop),
        migrations.RunSQL(
            "alter table products_product add column if not exists embedding vector(1024)",
            reverse_sql="alter table products_product drop column if exists embedding",
        ),
        migrations.RunSQL(
            "create index if not exists products_product_ai_text_trgm_idx "
            "on products_product using gin (ai_text gin_trgm_ops)",
            reverse_sql="drop index if exists products_product_ai_text_trgm_idx",
        ),
        migrations.RunSQL(
            "create index if not exists products_product_embedding_hnsw_idx "
            "on products_product using hnsw (embedding vector_cosine_ops)",
            reverse_sql="drop index if exists products_product_embedding_hnsw_idx",
        ),
    ]
