import asyncio
import logging
import os
from typing import Any

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
    headers = {"X-Internal-Api-Token": INTERNAL_API_TOKEN} if INTERNAL_API_TOKEN else {}
    async with httpx.AsyncClient(timeout=600) as client:
        response = await client.post(f"{DJANGO_API_BASE_URL}/api/chat/", json=payload, headers=headers)
        response.raise_for_status()
        return str(response.json().get("answer") or "Не удалось получить ответ.")


async def main() -> None:
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    dp = Dispatcher()

    async def handle(message: Message, text: str) -> None:
        if message.from_user is None:
            return
        if len(text) > 2000:
            await message.answer("Сообщение слишком длинное. Отправьте текст до 2000 символов.")
            return
        payload = {
            "message": text,
            "telegram_id": message.from_user.id,
            "telegram_message_id": message.message_id,
            "username": message.from_user.username or "",
            "first_name": message.from_user.first_name or "",
            "last_name": message.from_user.last_name or "",
        }
        try:
            answer = await call_chat(payload)
        except Exception:
            logger.exception("Failed to handle Telegram message")
            answer = "Сервис временно недоступен. Попробуйте позже."
        await message.answer(answer)

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
