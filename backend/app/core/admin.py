from django.contrib import admin
from .models import ChatMessage, EmbeddingRun, QAItem, SearchThresholdProfile, TelegramUser


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
