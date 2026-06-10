import logging

from celery.result import AsyncResult
from django.conf import settings
from django.http import HttpRequest, HttpResponse
from django.db.models import Q
from rest_framework.decorators import api_view
from rest_framework.response import Response

from app.content.models import Source

from . import llm
from .models import TelegramUser
from .pipeline import answer_user_message
from .search import health as health_data, search as search_service

logger = logging.getLogger(__name__)


def truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def index(request: HttpRequest) -> HttpResponse:
    return HttpResponse("Telegram RAG bot backend. Admin: /admin/")


@api_view(["GET"])
def health(request):
    return Response(health_data())


def internal_allowed(request) -> bool:
    expected = settings.INTERNAL_API_TOKEN
    return not expected or request.headers.get("X-Internal-Api-Token") == expected


@api_view(["POST"])
def chat(request):
    request_id = str(request.data.get("request_id") or request.headers.get("X-Request-ID") or "-")
    if not internal_allowed(request):
        logger.warning("request_id=%s stage=django event=chat_forbidden", request_id)
        return Response({"error": "forbidden"}, status=403)
    message = str(request.data.get("message") or "").strip()
    if not message:
        logger.info("request_id=%s stage=django event=chat_rejected reason=empty_message", request_id)
        return Response({"error": "message is required"}, status=400)
    logger.info(
        "request_id=%s stage=bot->django event=chat_received telegram_id=%s message_id=%s text_len=%s",
        request_id,
        request.data.get("telegram_id"),
        request.data.get("telegram_message_id"),
        len(message),
    )
    user, _ = TelegramUser.objects.update_or_create(
        telegram_id=int(request.data.get("telegram_id") or 0),
        defaults={
            "username": str(request.data.get("username") or ""),
            "first_name": str(request.data.get("first_name") or ""),
            "last_name": str(request.data.get("last_name") or ""),
        },
    )
    result = answer_user_message(
        user,
        message,
        request.data.get("telegram_message_id"),
        request_id=request_id,
        debug_requested=truthy(request.data.get("debug")) and user.is_superuser,
    )
    logger.info(
        "request_id=%s stage=django->bot event=chat_done route=%s answer_len=%s",
        request_id,
        result.get("metadata", {}).get("route"),
        len(str(result.get("answer") or "")),
    )
    return Response(result)


@api_view(["POST"])
def search(request):
    request_id = str(request.data.get("request_id") or request.headers.get("X-Request-ID") or "-")
    phrase = str(request.data.get("query") or request.data.get("message") or "").strip()
    if not phrase:
        return Response({"error": "query is required"}, status=400)
    logger.info("request_id=%s stage=django event=manual_search query_len=%s", request_id, len(phrase))
    classification = llm.classify_message(phrase, [], request_id=request_id)
    result = search_service(phrase, request_id=request_id, classification=classification)
    result["classification"] = classification
    return Response(result)


@api_view(["POST"])
def prepare_index(request):
    logger.info("request_id=- stage=django event=prepare_index_requested")
    return Response({"status": "skipped", "reason": "embeddings_removed"})


@api_view(["GET"])
def task_status(request, task_id: str):
    task = AsyncResult(task_id)
    payload = {"task_id": task_id, "state": task.state}
    if task.ready():
        try:
            payload["result"] = task.get(timeout=1)
        except Exception as exc:
            payload["error"] = str(exc)
    return Response(payload)


@api_view(["GET"])
def questions(request):
    q = str(request.query_params.get("q") or "")
    qs = Source.objects.select_related("classifier_class").order_by("id")
    if q:
        qs = qs.filter(Q(body__icontains=q) | Q(quick_phrases__phrase__icontains=q)).distinct()
    return Response({
        "items": [
            {
                "id": item.id,
                "classifier_class_id": item.classifier_class_id,
                "class_slug": item.classifier_class.slug if item.classifier_class_id else None,
                "class_title": item.classifier_class.title if item.classifier_class_id else None,
                "question_ru": item.body,
                "answer_ru": item.body,
                "body": item.body,
            }
            for item in qs[:100]
        ]
    })
