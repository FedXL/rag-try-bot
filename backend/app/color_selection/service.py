from typing import Any

from app.core import llm
from app.core.text import normalize_text


COLOR_HINTS = [
    "помещение",
    "комната",
    "стены",
    "фасад",
    "пол",
    "потолок",
    "интерьер",
    "экстерьер",
    "свет",
    "освещение",
    "оттенок",
    "цвет",
    "колеровка",
    "палитра",
]


def needs_clarification(message: str) -> bool:
    normalized = normalize_text(message)
    if len(normalized) < 20:
        return True
    return sum(1 for hint in COLOR_HINTS if hint in normalized) < 2


def fallback_answer() -> str:
    return (
        "Для подбора цвета уточните, пожалуйста: что красим, помещение или фасад, "
        "какой сейчас свет и какой эффект нужен - спокойный, теплый, яркий или нейтральный."
    )


def answer_color_selection_message(
    message: str,
    classification: dict[str, Any] | None = None,
    history: list[dict[str, str]] | None = None,
    request_id: str = "-",
) -> dict[str, Any]:
    if not llm.has_llm() or needs_clarification(message):
        answer = fallback_answer()
        engine = "fallback"
    else:
        history_text = "\n".join(f"{item['role']}: {item['content']}" for item in (history or [])[-6:])
        prompt = f"""
Ты консультант магазина "Центр Красок" по подбору цвета.

Ответь коротко и практично. Если данных недостаточно, задай 2-3 уточняющих вопроса.
Не подбирай конкретный товар и не называй цену. Не выдумывай наличие.

История:
{history_text or "(пусто)"}

Сообщение пользователя:
{message}
"""
        answer = llm.chat(
            [
                {"role": "system", "content": "Ты помогаешь выбрать цвет краски и оттенок для интерьера или фасада."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
            request_id=request_id,
            purpose="color_selection",
        ).strip()
        engine = "llm"

    return {
        "answer": answer,
        "metadata": {
            "route": "color_selection",
            "intent": (classification or {}).get("intent") or "color_selection",
            "engine": engine,
        },
        "debug": {
            "route": "color_selection",
            "engine": engine,
            "needs_clarification": needs_clarification(message),
        },
    }
