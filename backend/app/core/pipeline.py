from typing import Any

from . import llm
from .models import ChatMessage, TelegramUser
from .search import search


def history_for_user(user: TelegramUser, limit: int = 6) -> list[dict[str, str]]:
    rows = list(user.messages.order_by("-created_at")[:limit])
    rows.reverse()
    return [{"role": row.role, "content": row.text} for row in rows]


def answer_user_message(user: TelegramUser, message: str, telegram_message_id: int | None = None) -> dict[str, Any]:
    inbound = ChatMessage.objects.create(user=user, telegram_message_id=telegram_message_id, role=ChatMessage.ROLE_USER, text=message)
    history = history_for_user(user)
    classification = llm.classify_message(message, history)
    metadata: dict[str, Any] = {"classification": classification, "inbound_message_id": inbound.id}
    if not classification.get("need_search"):
        answer = llm.direct_answer(message, history)
        metadata["route"] = "direct"
    else:
        query = llm.rewrite_query(message, history) if classification.get("need_rewrite") else message
        result = search(query)
        metadata["route"] = "rag"
        metadata["query"] = query
        metadata["search"] = {"decision": result["decision"], "candidate_ids": [item.get("id") for item in result.get("candidates", [])]}
        answer = (
            llm.grounded_answer(message, result.get("candidates", []), history)
            if result["decision"] == "FOUND"
            else "В базе знаний не найдено достаточно информации по этому вопросу."
        )
    outbound = ChatMessage.objects.create(user=user, role=ChatMessage.ROLE_ASSISTANT, text=answer, metadata=metadata)
    return {"answer": answer, "classification": classification, "metadata": metadata, "message_id": outbound.id}
