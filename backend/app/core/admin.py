from django.contrib import admin
from .models import ArticleChunk, ChatMessage, EmbeddingRun, QAItem, QuickPhrase, SearchThresholdProfile, TelegramUser


@admin.register(QAItem)
class QAItemAdmin(admin.ModelAdmin):
    list_display = ("source_number", "short_question", "short_answer")
    search_fields = ("question_ru", "answer_ru")
    ordering = ("source_number",)

    def short_question(self, obj):
        return obj.question_ru[:120]

    def short_answer(self, obj):
        return obj.answer_ru[:120]


admin.site.register(SearchThresholdProfile)
admin.site.register(TelegramUser)
admin.site.register(ChatMessage)
admin.site.register(EmbeddingRun)


@admin.register(ArticleChunk)
class ArticleChunkAdmin(admin.ModelAdmin):
    list_display = ("section", "article_key", "chunk_index", "title", "is_active")
    list_filter = ("section", "is_active")
    search_fields = ("article_key", "title", "chunk_text", "source_url")
    ordering = ("section", "article_key", "chunk_index")


@admin.register(QuickPhrase)
class QuickPhraseAdmin(admin.ModelAdmin):
    list_display = ("section", "short_phrase", "article_key", "priority", "is_active")
    list_filter = ("section", "is_active")
    search_fields = ("phrase", "normalized_phrase", "article_key")
    ordering = ("priority", "id")

    def short_phrase(self, obj):
        return obj.phrase[:120]
