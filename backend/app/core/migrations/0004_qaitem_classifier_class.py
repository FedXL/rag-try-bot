from django.db import migrations, models
import django.db.models.deletion


def backfill_qaitem_classes(apps, schema_editor):
    QAItem = apps.get_model("core", "QAItem")
    ClassifierClass = apps.get_model("core", "ClassifierClass")
    Article = apps.get_model("core", "Article")
    Product = apps.get_model("core", "Product")

    classes = {row.slug: row for row in ClassifierClass.objects.all()}
    product_class = classes.get("product")
    mixed_class = classes.get("mixed")

    articles_by_qaitem = {}
    for article in Article.objects.exclude(metadata__legacy_qaitem_id__isnull=True).select_related("classifier_class"):
        legacy_id = (article.metadata or {}).get("legacy_qaitem_id")
        if legacy_id:
            articles_by_qaitem[int(legacy_id)] = article.classifier_class_id

    product_qaitem_ids = set()
    for product in Product.objects.exclude(metadata__legacy_qaitem_id__isnull=True):
        legacy_id = (product.metadata or {}).get("legacy_qaitem_id")
        if legacy_id:
            product_qaitem_ids.add(int(legacy_id))

    for item in QAItem.objects.filter(classifier_class__isnull=True).iterator():
        class_id = None
        if item.id in product_qaitem_ids and product_class:
            class_id = product_class.id
        elif item.id in articles_by_qaitem:
            class_id = articles_by_qaitem[item.id]
        elif mixed_class:
            class_id = mixed_class.id
        if class_id:
            QAItem.objects.filter(id=item.id).update(classifier_class_id=class_id)


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0003_content_classes_products"),
    ]

    operations = [
        migrations.AddField(
            model_name="qaitem",
            name="classifier_class",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="qa_items",
                to="core.classifierclass",
            ),
        ),
        migrations.RunPython(backfill_qaitem_classes, reverse_code=migrations.RunPython.noop),
    ]
