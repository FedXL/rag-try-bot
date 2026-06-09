import logging
from time import perf_counter
from typing import Any

from . import llm
from .models import ChatMessage, TelegramUser
from .search import search

logger = logging.getLogger(__name__)


def history_for_user(user: TelegramUser, limit: int = 6) -> list[dict[str, str]]:
    rows = list(user.messages.order_by("-created_at")[:limit])
    rows.reverse()
    return [{"role": row.role, "content": row.text} for row in rows]


def compact_search_debug(result: dict[str, Any], query: str) -> dict[str, Any]:
    return {
        "query": query,
        "decision": result.get("decision"),
        "retriever_breakdown": result.get("retriever_breakdown", {}),
        "candidates_count": len(result.get("candidates", [])),
        "candidates": [
            {
                "id": item.get("id"),
                "source_number": item.get("number"),
                "score": item.get("score"),
                "reranker_score": item.get("reranker_score"),
                "lexical_signal": item.get("lexical_signal"),
                "question": item.get("question"),
            }
            for item in result.get("candidates", [])[:3]
        ],
    }


def answer_user_message(
    user: TelegramUser,
    message: str,
    telegram_message_id: int | None = None,
    request_id: str = "-",
    debug_requested: bool = False,
) -> dict[str, Any]:
    started = perf_counter()
    logger.info("request_id=%s stage=pipeline event=start user_id=%s text_len=%s", request_id, user.telegram_id, len(message))
    inbound = ChatMessage.objects.create(user=user, telegram_message_id=telegram_message_id, role=ChatMessage.ROLE_USER, text=message)
    history = history_for_user(user)
    logger.info("request_id=%s stage=pipeline event=history_loaded messages=%s", request_id, len(history))
    classification = llm.classify_message(message, history, request_id=request_id)
    metadata: dict[str, Any] = {"classification": classification, "inbound_message_id": inbound.id}
    debug: dict[str, Any] = {"classification": classification} if debug_requested else {}
    logger.info(
        "request_id=%s stage=classifier event=classified need_search=%s need_rewrite=%s query_type=%s reason=%s engine=%s",
        request_id,
        classification.get("need_search"),
        classification.get("need_rewrite"),
        classification.get("query_type"),
        classification.get("reason"),
        classification.get("engine"),
    )
    if not classification.get("need_search"):
        answer = llm.direct_answer(message, history, request_id=request_id)
        metadata["route"] = "direct"
        if debug_requested:
            debug["search"] = {"skipped": True, "reason": "classifier.need_search=false"}
        logger.info("request_id=%s stage=pipeline event=route_selected route=direct", request_id)
    else:
        query = llm.rewrite_query(message, history, request_id=request_id) if classification.get("need_rewrite") else message
        logger.info("request_id=%s stage=pipeline event=route_selected route=rag query_len=%s", request_id, len(query))
        result = search(query, request_id=request_id)
        metadata["route"] = "rag"
        metadata["query"] = query
        metadata["search"] = {"decision": result["decision"], "candidate_ids": [item.get("id") for item in result.get("candidates", [])]}
        if debug_requested:
            debug["search"] = compact_search_debug(result, query)
        answer = (
            llm.grounded_answer(message, result.get("candidates", []), history, request_id=request_id)
            if result["decision"] == "FOUND"
            else "В базе знаний не найдено достаточно информации по этому вопросу."
        )
        logger.info(
            "request_id=%s stage=pipeline event=rag_answer_prepared decision=%s candidates=%s",
            request_id,
            result["decision"],
            len(result.get("candidates", [])),
        )
    outbound = ChatMessage.objects.create(user=user, role=ChatMessage.ROLE_ASSISTANT, text=answer, metadata=metadata)
    logger.info(
        "request_id=%s stage=pipeline event=done route=%s answer_len=%s duration_ms=%s",
        request_id,
        metadata.get("route"),
        len(answer),
        round((perf_counter() - started) * 1000),
    )
    response = {"answer": answer, "classification": classification, "metadata": metadata, "message_id": outbound.id}
    if debug_requested:
        response["debug"] = debug
    return response
