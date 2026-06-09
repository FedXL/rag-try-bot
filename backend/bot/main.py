import asyncio
import logging
import os
from time import perf_counter
from typing import Any
from uuid import uuid4

import httpx
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import Message

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
DJANGO_API_BASE_URL = os.environ.get("DJANGO_API_BASE_URL", "http://web:9001")
INTERNAL_API_TOKEN = os.environ.get("INTERNAL_API_TOKEN", "")
BOT_DEBUG_MESSAGES = os.environ.get("BOT_DEBUG_MESSAGES", "0") == "1"
TELEGRAM_MESSAGE_LIMIT = 3900

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def call_chat(payload: dict[str, Any]) -> dict[str, Any]:
    request_id = str(payload.get("request_id") or "-")
    headers = {"X-Internal-Api-Token": INTERNAL_API_TOKEN} if INTERNAL_API_TOKEN else {}
    headers["X-Request-ID"] = request_id
    started = perf_counter()
    logger.info(
        "request_id=%s stage=bot->django event=chat_post url=%s telegram_id=%s message_id=%s text_len=%s debug=%s",
        request_id,
        DJANGO_API_BASE_URL,
        payload.get("telegram_id"),
        payload.get("telegram_message_id"),
        len(str(payload.get("message") or "")),
        payload.get("debug"),
    )
    async with httpx.AsyncClient(timeout=600) as client:
        response = await client.post(f"{DJANGO_API_BASE_URL}/api/chat/", json=payload, headers=headers)
        response.raise_for_status()
        data = response.json()
        logger.info(
            "request_id=%s stage=django->bot event=chat_response status=%s answer_len=%s has_debug=%s duration_ms=%s",
            request_id,
            response.status_code,
            len(str(data.get("answer") or "")),
            "debug" in data,
            round((perf_counter() - started) * 1000),
        )
        return data


def fmt_bool(value: Any) -> str:
    return "true" if bool(value) else "false"


def fmt_score(value: Any) -> str:
    if value is None:
        return "-"
    try:
        return f"{float(value):.4f}"
    except (TypeError, ValueError):
        return str(value)


def short_text(value: Any, limit: int = 220) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def format_classifier_debug(debug: dict[str, Any]) -> str:
    classification = debug.get("classification") or {}
    lines = [
        "[debug] Классификатор",
        f"engine: {classification.get('engine') or '-'}",
        f"need_search: {fmt_bool(classification.get('need_search'))}",
        f"need_rewrite: {fmt_bool(classification.get('need_rewrite'))}",
        f"query_type: {classification.get('query_type') or '-'}",
        f"reason: {classification.get('reason') or '-'}",
    ]
    engine = classification.get("engine")
    if engine in {"fallback", "fallback_after_llm_error"}:
        lines.extend([
            "",
            "Fallback-классификатор - это простой rule-based классификатор без LLM.",
            "Он используется, когда XAI_API_KEY не задан или LLM-классификатор упал.",
            "Он смотрит на приветствия, вопросительные маркеры и длину сообщения.",
        ])
    return "\n".join(lines)


def format_search_debug(debug: dict[str, Any]) -> str:
    search = debug.get("search") or {}
    if search.get("skipped"):
        return "\n".join([
            "[debug] Поиск",
            f"Не запускался: {search.get('reason') or 'classifier.need_search=false'}",
        ])

    breakdown = search.get("retriever_breakdown") or {}
    lines = [
        "[debug] Поиск",
        f"query: {short_text(search.get('query'), 260)}",
        f"decision: {search.get('decision') or '-'}",
        f"retrievers: lexical={breakdown.get('lexical', 0)}, vector={breakdown.get('vector', 0)}",
        f"candidates: {search.get('candidates_count', 0)}",
    ]
    candidates = search.get("candidates") or []
    for index, item in enumerate(candidates[:3], start=1):
        lines.extend([
            "",
            f"{index}. id={item.get('id')} #source_number={item.get('source_number')}",
            f"   score={fmt_score(item.get('score'))} reranker_score={fmt_score(item.get('reranker_score'))} lexical_signal={fmt_score(item.get('lexical_signal'))}",
            f"   question: {short_text(item.get('question'))}",
        ])
    if not candidates:
        lines.append("top candidates: пусто")
    return "\n".join(lines)


def split_telegram_message(text: str) -> list[str]:
    if len(text) <= TELEGRAM_MESSAGE_LIMIT:
        return [text]
    chunks = []
    current = []
    current_len = 0
    for line in text.splitlines():
        line_len = len(line) + 1
        if current and current_len + line_len > TELEGRAM_MESSAGE_LIMIT:
            chunks.append("\n".join(current))
            current = []
            current_len = 0
        current.append(line)
        current_len += line_len
    if current:
        chunks.append("\n".join(current))
    return chunks


async def send_text(message: Message, text: str) -> None:
    for chunk in split_telegram_message(text):
        await message.answer(chunk)


async def main() -> None:
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    dp = Dispatcher()

    async def handle(message: Message, text: str) -> None:
        if message.from_user is None:
            return
        request_id = uuid4().hex[:12]
        logger.info(
            "request_id=%s stage=telegram->bot event=message_received telegram_id=%s username=%s message_id=%s text_len=%s",
            request_id,
            message.from_user.id,
            message.from_user.username or "",
            message.message_id,
            len(text),
        )
        if len(text) > 2000:
            logger.info("request_id=%s stage=bot event=rejected reason=message_too_long", request_id)
            await message.answer("Сообщение слишком длинное. Отправьте текст до 2000 символов.")
            return
        payload = {
            "request_id": request_id,
            "debug": BOT_DEBUG_MESSAGES,
            "message": text,
            "telegram_id": message.from_user.id,
            "telegram_message_id": message.message_id,
            "username": message.from_user.username or "",
            "first_name": message.from_user.first_name or "",
            "last_name": message.from_user.last_name or "",
        }
        try:
            data = await call_chat(payload)
            debug = data.get("debug") if BOT_DEBUG_MESSAGES else None
            if isinstance(debug, dict):
                await send_text(message, format_classifier_debug(debug))
                await send_text(message, format_search_debug(debug))
            answer = str(data.get("answer") or "Не удалось получить ответ.")
        except Exception as exc:
            logger.exception("request_id=%s stage=bot event=chat_failed error=%s", request_id, exc)
            answer = "Сервис временно недоступен. Попробуйте позже."
        await send_text(message, answer)
        logger.info("request_id=%s stage=bot->telegram event=answer_sent answer_len=%s debug=%s", request_id, len(answer), BOT_DEBUG_MESSAGES)

    @dp.message(CommandStart())
    async def start(message: Message) -> None:
        await handle(message, "/start")

    @dp.message(F.text)
    async def text(message: Message) -> None:
        await handle(message, (message.text or "").strip())

    logger.info("Starting Telegram bot polling debug_messages=%s", BOT_DEBUG_MESSAGES)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
