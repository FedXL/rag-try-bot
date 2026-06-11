from typing import Any

from app.core.text import normalize_text


TOP_COLORS_URL = "https://centr-krasok.kz/tinting/"
PSYCHOTYPE_URL = "https://centr-krasok.kz/colors/psychotype/"
PALETTES_URL = "https://centr-krasok.kz/tints/"


OPTIONS = {
    "top_colors": {
        "title": "Топ-20 популярных цветов",
        "text": (
            "Хороший старт, если хочется быстро посмотреть самые востребованные оттенки. "
            "Здесь собраны цвета, которые часто выбирают для современных интерьеров."
        ),
        "url": TOP_COLORS_URL,
    },
    "psychotype": {
        "title": "Подбор цвета по психотипу",
        "text": (
            "Подойдет, если хочется выбрать цвет по настроению, характеру и ощущению комнаты. "
            "Такой подход помогает найти оттенок, который будет радовать каждый день."
        ),
        "url": PSYCHOTYPE_URL,
    },
    "palettes": {
        "title": "Палитры оттенков",
        "text": (
            "Удобный вариант, когда хочется спокойно сравнить разные гаммы и найти точное сочетание. "
            "Можно вдохновиться готовыми палитрами и выбрать свой тон."
        ),
        "url": PALETTES_URL,
    },
}


def detect_option(message: str, history: list[dict[str, str]] | None = None) -> str | None:
    normalized = normalize_text(message)
    history_text = normalize_text(" ".join(item.get("content", "") for item in (history or [])[-4:]))

    if any(word in normalized for word in ("психотип", "характер", "настроение", "ощущение")):
        return "psychotype"
    if any(word in normalized for word in ("топ", "популяр", "20", "двадц")):
        return "top_colors"
    if any(word in normalized for word in ("палитр", "оттен", "гамм", "сочетан", "3 4", "3-4")):
        if "психотип" in history_text and not any(word in normalized for word in ("палитр", "гамм", "сочетан")):
            return "psychotype"
        return "palettes"
    return None


def format_option(option_key: str, intro: str | None = None) -> str:
    option = OPTIONS[option_key]
    lines = []
    if intro:
        lines.append(intro)
        lines.append("")
    lines.extend(
        [
            option["title"],
            option["text"],
            option["url"],
        ]
    )
    return "\n".join(lines)


def format_all_options() -> str:
    blocks = [
        "Здорово, у нас есть несколько приятных способов подобрать цвет.",
        "",
    ]
    for option_key in ("top_colors", "psychotype", "palettes"):
        blocks.append(format_option(option_key))
        blocks.append("")
    blocks.append("Можно открыть любой вариант и выбрать оттенок на сайте.")
    return "\n".join(blocks).strip()


def answer_color_selection_message(
    message: str,
    classification: dict[str, Any] | None = None,
    history: list[dict[str, str]] | None = None,
    request_id: str = "-",
) -> dict[str, Any]:
    option_key = detect_option(message, history)
    if option_key:
        answer = format_option(option_key, "Здорово, у нас есть такой вариант:")
    else:
        answer = format_all_options()

    return {
        "answer": answer,
        "metadata": {
            "route": "color_selection",
            "intent": (classification or {}).get("intent") or "color_selection",
            "engine": "link_flow",
            "option": option_key or "all",
        },
        "debug": {
            "route": "color_selection",
            "engine": "link_flow",
            "option": option_key or "all",
        },
    }
