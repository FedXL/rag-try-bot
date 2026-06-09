from celery.result import AsyncResult
from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .models import QAItem, TelegramUser
from .pipeline import answer_user_message
from .search import health as health_data, prepare_async, search as search_service


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
    if not internal_allowed(request):
        return Response({"error": "forbidden"}, status=403)
    message = str(request.data.get("message") or "").strip()
    if not message:
        return Response({"error": "message is required"}, status=400)
    user, _ = TelegramUser.objects.update_or_create(
        telegram_id=int(request.data.get("telegram_id") or 0),
        defaults={"username": str(request.data.get("username") or ""), "first_name": str(request.data.get("first_name") or ""), "last_name": str(request.data.get("last_name") or "")},
    )
    return Response(answer_user_message(user, message, request.data.get("telegram_message_id")))


@api_view(["POST"])
def search(request):
    phrase = str(request.data.get("query") or request.data.get("message") or "").strip()
    if not phrase:
        return Response({"error": "query is required"}, status=400)
    return Response(search_service(phrase))


@api_view(["POST"])
def prepare_index(request):
    task = prepare_async()
    return Response({"task_id": task.id, "state": task.state})


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
    qs = QAItem.objects.all().order_by("source_number")
    if q:
        qs = qs.filter(question_ru__icontains=q)
    return Response({"items": [{"id": item.id, "source_number": item.source_number, "question_ru": item.question_ru, "answer_ru": item.answer_ru} for item in qs[:100]]})
