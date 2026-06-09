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

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def call_chat(payload: dict[str, Any]) -> str:
    request_id = str(payload.get("request_id") or "-")
    headers = {"X-Internal-Api-Token": INTERNAL_API_TOKEN} if INTERNAL_API_TOKEN else {}
    headers["X-Request-ID"] = request_id
    started = perf_counter()
    logger.info(
        "request_id=%s stage=bot->django event=chat_post url=%s telegram_id=%s message_id=%s text_len=%s",
        request_id,
        DJANGO_API_BASE_URL,
        payload.get("telegram_id"),
        payload.get("telegram_message_id"),
        len(str(payload.get("message") or "")),
    )
    async with httpx.AsyncClient(timeout=600) as client:
        response = await client.post(f"{DJANGO_API_BASE_URL}/api/chat/", json=payload, headers=headers)
        response.raise_for_status()
        answer = str(response.json().get("answer") or "Не удалось получить ответ.")
        logger.info(
            "request_id=%s stage=django->bot event=chat_response status=%s answer_len=%s duration_ms=%s",
            request_id,
            response.status_code,
            len(answer),
            round((perf_counter() - started) * 1000),
        )
        return answer


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
            "message": text,
            "telegram_id": message.from_user.id,
            "telegram_message_id": message.message_id,
            "username": message.from_user.username or "",
            "first_name": message.from_user.first_name or "",
            "last_name": message.from_user.last_name or "",
        }
        try:
            answer = await call_chat(payload)
        except Exception as exc:
            logger.exception("request_id=%s stage=bot event=chat_failed error=%s", request_id, exc)
            answer = "Сервис временно недоступен. Попробуйте позже."
        await message.answer(answer)
        logger.info("request_id=%s stage=bot->telegram event=answer_sent answer_len=%s", request_id, len(answer))

    @dp.message(CommandStart())
    async def start(message: Message) -> None:
        await handle(message, "/start")

    @dp.message(F.text)
    async def text(message: Message) -> None:
        await handle(message, (message.text or "").strip())

    logger.info("Starting Telegram bot polling")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
