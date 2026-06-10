from django.contrib import admin
from django.db.models import Count

from .models import ClassifierClass, QuickPhrase, Source


@admin.register(ClassifierClass)
class ClassifierClassAdmin(admin.ModelAdmin):
    list_display = ("slug", "title", "kind", "source_count", "is_active")
    list_filter = ("kind", "is_active")
    search_fields = ("slug", "title", "description")
    ordering = ("slug",)

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(source_count_value=Count("sources", distinct=True))

    def source_count(self, obj):
        return obj.source_count_value

    source_count.short_description = "Sources"


class QuickPhraseInline(admin.TabularInline):
    model = QuickPhrase
    extra = 1
    fields = ("phrase", "priority", "is_active")


@admin.register(Source)
class SourceAdmin(admin.ModelAdmin):
    list_display = ("id", "classifier_class", "body_preview", "quick_phrases_preview", "quick_phrases_count", "is_active")
    list_editable = ("classifier_class", "is_active")
    list_filter = ("classifier_class", "is_active")
    search_fields = ("body", "source_url", "quick_phrases__phrase")
    ordering = ("classifier_class__slug", "id")
    inlines = [QuickPhraseInline]

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(quick_phrase_count=Count("quick_phrases", distinct=True))

    def quick_phrases_count(self, obj):
        return obj.quick_phrase_count

    def quick_phrases_preview(self, obj):
        return ", ".join(obj.quick_phrases.order_by("priority", "id").values_list("phrase", flat=True)[:3])

    def body_preview(self, obj):
        return " ".join((obj.body or "").split())[:160]


@admin.register(QuickPhrase)
class QuickPhraseAdmin(admin.ModelAdmin):
    list_display = ("source", "short_phrase", "priority", "is_active")
    list_filter = ("source__classifier_class", "is_active")
    search_fields = ("phrase", "normalized_phrase", "source__body")
    ordering = ("priority", "id")

    def short_phrase(self, obj):
        return obj.phrase[:120]
