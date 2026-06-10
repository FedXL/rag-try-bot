import logging
from time import perf_counter
from typing import Any

from . import llm
from .models import ChatMessage, TelegramUser
from .search import search
from app.color_selection.service import answer_color_selection_message
from app.products.service import answer_product_message

logger = logging.getLogger(__name__)


def notify_tech(user: TelegramUser, request_id: str, stage: str, event: str, details: dict[str, Any] | None = None) -> None:
    if not user.is_superuser:
        return

    try:
        from .tasks import send_telegram_tech_message_task

        send_telegram_tech_message_task.delay(
            {
                "request_id": request_id,
                "stage": stage,
                "event": event,
                "telegram_id": user.telegram_id,
                "username": user.username,
                "is_superuser": user.is_superuser,
                "details": details or {},
            }
        )
    except Exception as exc:
        logger.warning("request_id=%s stage=telegram_notify event=enqueue_failed error=%s", request_id, exc)


def history_for_user(user: TelegramUser, limit: int = 6) -> list[dict[str, str]]:
    rows = list(user.messages.order_by("-created_at")[:limit])
    rows.reverse()
    return [{"role": row.role, "content": row.text} for row in rows]


def compact_search_debug(result: dict[str, Any], query: str) -> dict[str, Any]:
    return {
        "query": query,
        "decision": result.get("decision"),
        "route": result.get("route"),
        "quick_phrase": result.get("quick_phrase"),
        "embedding_status": result.get("embedding_status"),
        "retriever_breakdown": result.get("retriever_breakdown", {}),
        "candidates_count": len(result.get("candidates", [])),
        "candidates": [
            {
                "id": item.get("id"),
                "content_type": item.get("content_type"),
                "source_key": item.get("source_key"),
                "article_key": item.get("article_key"),
                "product_key": item.get("product_key"),
                "class_slug": item.get("class_slug"),
                "section": item.get("section"),
                "chunk_index": item.get("chunk_index"),
                "score": item.get("score"),
                "reranker_score": item.get("reranker_score"),
                "lexical_signal": item.get("lexical_signal"),
                "title": item.get("title") or item.get("question"),
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
    notify_tech(user, request_id, "pipeline", "start", {"text_len": len(message), "message": message[:300]})
    if message.strip() == "/start":
        deleted, _ = ChatMessage.objects.filter(user=user).delete()
        notify_tech(user, request_id, "pipeline", "history_reset", {"deleted_messages": deleted})

    inbound = ChatMessage.objects.create(user=user, telegram_message_id=telegram_message_id, role=ChatMessage.ROLE_USER, text=message)
    notify_tech(user, request_id, "pipeline", "message_saved", {"chat_message_id": inbound.id, "telegram_message_id": telegram_message_id})
    if message.strip() == "/start":
        answer = llm.direct_answer(message, [], request_id=request_id)
        metadata: dict[str, Any] = {"route": "direct", "command": "start", "inbound_message_id": inbound.id}
        outbound = ChatMessage.objects.create(user=user, role=ChatMessage.ROLE_ASSISTANT, text=answer, metadata=metadata)
        duration_ms = round((perf_counter() - started) * 1000)
        notify_tech(user, request_id, "pipeline", "response_saved", {"chat_message_id": outbound.id, "route": metadata.get("route")})
        notify_tech(user, request_id, "pipeline", "done", {"route": metadata.get("route"), "answer_len": len(answer), "duration_ms": duration_ms})
        logger.info(
            "request_id=%s stage=pipeline event=done route=direct command=start answer_len=%s duration_ms=%s",
            request_id,
            len(answer),
            duration_ms,
        )
        classification = {"need_search": False, "class_slug": "none", "section": "none", "intent": "start_command", "engine": "command"}
        response = {"answer": answer, "classification": classification, "metadata": metadata, "message_id": outbound.id}
        if debug_requested:
            response["debug"] = {"classification": classification, "search": {"skipped": True, "reason": "command.start"}}
        return response

    history = history_for_user(user)
    logger.info("request_id=%s stage=pipeline event=history_loaded messages=%s", request_id, len(history))
    notify_tech(user, request_id, "pipeline", "history_loaded", {"messages": len(history)})

    notify_tech(user, request_id, "classifier", "start", {"text_len": len(message)})
    classification = llm.classify_message(message, history, request_id=request_id)
    metadata: dict[str, Any] = {"classification": classification, "inbound_message_id": inbound.id}
    debug: dict[str, Any] = {"classification": classification} if debug_requested else {}
    logger.info(
        "request_id=%s stage=classifier event=classified need_search=%s need_rewrite=%s query_type=%s section=%s intent=%s reason=%s engine=%s",
        request_id,
        classification.get("need_search"),
        classification.get("need_rewrite"),
        classification.get("query_type"),
        classification.get("section"),
        classification.get("intent"),
        classification.get("reason"),
        classification.get("engine"),
    )
    notify_tech(
        user,
        request_id,
        "classifier",
        "done",
        {
            "engine": classification.get("engine"),
            "need_search": classification.get("need_search"),
            "class_slug": classification.get("class_slug") or classification.get("section"),
            "intent": classification.get("intent"),
            "confidence": classification.get("confidence"),
            "reason": classification.get("reason"),
        },
    )

    if not classification.get("need_search"):
        notify_tech(user, request_id, "pipeline", "route_selected", {"route": "direct", "reason": "classifier.need_search=false"})
        notify_tech(user, request_id, "answer", "direct_start", {})
        answer = llm.direct_answer(message, history, request_id=request_id)
        metadata["route"] = "direct"
        if debug_requested:
            debug["search"] = {"skipped": True, "reason": "classifier.need_search=false"}
        logger.info("request_id=%s stage=pipeline event=route_selected route=direct", request_id)
        notify_tech(user, request_id, "answer", "direct_done", {"answer_len": len(answer)})
    else:
        query = str(classification.get("rewritten_query") or "").strip()
        if not query:
            if classification.get("need_rewrite"):
                notify_tech(user, request_id, "query_rewrite", "start", {})
            query = llm.rewrite_query(message, history, request_id=request_id) if classification.get("need_rewrite") else message
            if classification.get("need_rewrite"):
                notify_tech(user, request_id, "query_rewrite", "done", {"query": query[:300], "query_len": len(query)})
        logger.info(
            "request_id=%s stage=pipeline event=route_selected route=rag query_len=%s intent=%s section=%s",
            request_id,
            len(query),
            classification.get("intent"),
            classification.get("section"),
        )
        notify_tech(
            user,
            request_id,
            "pipeline",
            "route_selected",
            {"route": "rag", "query_len": len(query), "class_slug": classification.get("class_slug") or classification.get("section")},
        )

        class_slug = str(classification.get("class_slug") or classification.get("section") or "")
        if class_slug in {"product", "catalog", "brands"}:
            notify_tech(user, request_id, "product", "start", {"query": query[:300], "intent": classification.get("intent")})
            product_result = answer_product_message(query, classification)
            answer = product_result["answer"]
            metadata["route"] = "product"
            metadata["query"] = query
            metadata["product"] = product_result["metadata"]
            if debug_requested:
                debug["product"] = product_result["debug"]
            notify_tech(
                user,
                request_id,
                "product",
                "done",
                {
                    "intent": product_result["metadata"].get("intent"),
                    "products": len(product_result["metadata"].get("product_ids", [])),
                    "answer_len": len(answer),
                },
            )
            logger.info(
                "request_id=%s stage=pipeline event=product_answer_prepared intent=%s products=%s",
                request_id,
                product_result["metadata"].get("intent"),
                len(product_result["metadata"].get("product_ids", [])),
            )
        elif class_slug == "color_selection":
            notify_tech(user, request_id, "color_selection", "start", {"query": query[:300], "intent": classification.get("intent")})
            color_result = answer_color_selection_message(query, classification, history, request_id=request_id)
            answer = color_result["answer"]
            metadata["route"] = "color_selection"
            metadata["query"] = query
            metadata["color_selection"] = color_result["metadata"]
            if debug_requested:
                debug["color_selection"] = color_result["debug"]
            notify_tech(
                user,
                request_id,
                "color_selection",
                "done",
                {
                    "intent": color_result["metadata"].get("intent"),
                    "engine": color_result["metadata"].get("engine"),
                    "answer_len": len(answer),
                },
            )
            logger.info(
                "request_id=%s stage=pipeline event=color_selection_answer_prepared intent=%s engine=%s",
                request_id,
                color_result["metadata"].get("intent"),
                color_result["metadata"].get("engine"),
            )
        else:

            notify_tech(user, request_id, "search", "start", {"query": query[:300]})
            result = search(query, request_id=request_id, classification=classification)
            notify_tech(
                user,
                request_id,
                "search",
                "done",
                {
                    "decision": result.get("decision"),
                    "route": result.get("route"),
                    "candidates": len(result.get("candidates", [])),
                    "top_type": (result.get("top_candidate") or {}).get("content_type"),
                    "top_class": (result.get("top_candidate") or {}).get("class_slug"),
                },
            )

            metadata["route"] = "rag"
            metadata["query"] = query
            metadata["search"] = {
                "decision": result["decision"],
                "route": result.get("route"),
                "quick_phrase": result.get("quick_phrase"),
                "candidate_ids": [item.get("id") for item in result.get("candidates", [])],
            }
            if debug_requested:
                debug["search"] = compact_search_debug(result, query)

            notify_tech(user, request_id, "answer", "grounded_start" if result["decision"] == "FOUND" else "not_found_start", {"decision": result["decision"]})
            answer = (
                llm.grounded_answer(message, result.get("candidates", []), history, request_id=request_id)
                if result["decision"] == "FOUND"
                else "В базе знаний не найдено достаточно информации по этому вопросу."
            )
            notify_tech(user, request_id, "answer", "done", {"answer_len": len(answer), "decision": result["decision"]})
            logger.info(
                "request_id=%s stage=pipeline event=rag_answer_prepared decision=%s route=%s candidates=%s",
                request_id,
                result["decision"],
                result.get("route"),
                len(result.get("candidates", [])),
            )

    outbound = ChatMessage.objects.create(user=user, role=ChatMessage.ROLE_ASSISTANT, text=answer, metadata=metadata)
    notify_tech(user, request_id, "pipeline", "response_saved", {"chat_message_id": outbound.id, "route": metadata.get("route")})
    duration_ms = round((perf_counter() - started) * 1000)
    logger.info(
        "request_id=%s stage=pipeline event=done route=%s answer_len=%s duration_ms=%s",
        request_id,
        metadata.get("route"),
        len(answer),
        duration_ms,
    )
    notify_tech(user, request_id, "pipeline", "done", {"route": metadata.get("route"), "answer_len": len(answer), "duration_ms": duration_ms})

    response = {"answer": answer, "classification": classification, "metadata": metadata, "message_id": outbound.id}
    if debug_requested:
        response["debug"] = debug
    return response
