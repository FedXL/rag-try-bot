from __future__ import annotations

import re
from typing import Any

from .text import normalize_text


GREETINGS = {"/start", "старт", "привет", "здравствуйте", "добрый день", "добрый вечер", "доброе утро"}

SECTION_MARKERS: dict[str, list[str]] = {
    "about": [
        "кто вы",
        "что за магазин",
        "чем занимаетесь",
        "что делаете",
        "что предлагаете",
        "о компании",
        "центр красок",
        "ассортимент",
        "20 брендов",
        "45000",
        "45 000",
        "экологичные",
        "безопасные",
        "сертифицированные",
    ],
    "product": [
        "каталог",
        "товар",
        "артикул",
        "sku",
        "краска",
        "эмаль",
        "лак",
        "масло",
        "грунтовка",
        "штукатурка",
        "молдинг",
        "обои",
        "лепнина",
        "бренд",
        "бренды",
        "производитель",
    ],
    "help": [
        "доставка",
        "оплата",
        "возврат",
        "обмен",
        "заказ",
        "как заказать",
        "самовывоз",
        "гарантия",
    ],
    "inspiration": ["вдохновение", "идеи", "интерьер", "дизайн", "тренды", "сочетание", "пример"],
    "color_selection": ["подбор цвета", "колеровка", "колеровать", "оттенок", "палитра", "цвет"],
    "partners": ["партнер", "партнерам", "сотрудничество", "дизайнер", "строитель", "прораб"],
    "glossary": ["глоссарий", "термин", "что значит", "что такое"],
    "contacts": ["контакты", "адрес", "телефон", "где находитесь", "как добраться", "режим работы"],
    "news_articles": ["новости", "статья", "статьи", "публикация", "обзор"],
}

QUESTION_MARKERS = [
    "как",
    "что",
    "где",
    "когда",
    "какой",
    "какая",
    "какие",
    "сколько",
    "можно",
    "нужно",
    "подскажите",
    "расскажите",
]

ARTICLE_CODE_RE = re.compile(r"\b[a-zа-я]*\d{3,}[a-zа-я0-9-]*\b", re.IGNORECASE)


def contains_any(text: str, markers: list[str]) -> bool:
    return any(normalize_text(marker) in text for marker in markers)


def detect_section(text: str) -> tuple[str, str, float]:
    scores: dict[str, int] = {}
    for section, markers in SECTION_MARKERS.items():
        score = sum(1 for marker in markers if normalize_text(marker) in text)
        if score:
            scores[section] = score

    if ARTICLE_CODE_RE.search(text):
        scores["product"] = scores.get("product", 0) + 4

    if "бренд" in text or "бренды" in text:
        scores["product"] = scores.get("product", 0) + 2

    if not scores:
        return "mixed", "unclear", 0.45

    section, score = sorted(scores.items(), key=lambda item: item[1], reverse=True)[0]
    intent_by_section = {
        "about": "about_company",
        "product": "product_lookup",
        "help": "help",
        "inspiration": "inspiration",
        "color_selection": "color_selection",
        "partners": "partners",
        "glossary": "glossary",
        "contacts": "contacts",
        "news_articles": "news_articles",
    }
    confidence = 0.95 if score >= 3 else 0.85 if score == 2 else 0.72
    return section, intent_by_section.get(section, "knowledge_base"), confidence


def extract_slots(text: str) -> dict[str, Any]:
    slots: dict[str, Any] = {}
    code_match = ARTICLE_CODE_RE.search(text)
    if code_match:
        slots["article_or_sku"] = code_match.group(0).upper()
    for brand in ["dulux", "marshall", "hammerite", "pinotex", "tikkurila", "oikos", "dufa", "kudo", "argile"]:
        if brand in text:
            slots["brand"] = brand
            break
    if "цена" in text or "стоимость" in text or "сколько стоит" in text:
        slots["price"] = True
    if "наличие" in text or "есть ли" in text or "в наличии" in text:
        slots["availability"] = True
    return slots


def classify_rule(message: str, history: list[dict[str, str]] | None = None, engine: str = "domain_rules") -> dict[str, Any]:
    text = normalize_text(message)
    if not text:
        return {
            "need_search": False,
            "need_rewrite": False,
            "query_type": "general_chat",
            "section": "none",
            "class_slug": "none",
            "intent": "empty",
            "search_scope": "none",
            "slots": {},
            "rewritten_query": message,
            "confidence": 1.0,
            "reason": "пустое сообщение",
            "engine": engine,
        }

    if text in GREETINGS:
        return {
            "need_search": False,
            "need_rewrite": False,
            "query_type": "greeting",
            "section": "none",
            "class_slug": "none",
            "intent": "greeting",
            "search_scope": "none",
            "slots": {},
            "rewritten_query": message,
            "confidence": 1.0,
            "reason": "приветствие",
            "engine": engine,
        }

    section, intent, confidence = detect_section(text)
    has_question_shape = contains_any(text, QUESTION_MARKERS) or "?" in message
    need_search = section != "mixed" or has_question_shape or len(text.split()) >= 4
    if not need_search:
        section = "none"
        intent = "general_chat"

    return {
        "need_search": bool(need_search),
        "need_rewrite": False,
        "query_type": "knowledge_base" if need_search else "general_chat",
        "section": section if need_search else "none",
        "class_slug": section if need_search else "none",
        "intent": intent,
        "search_scope": section if need_search else "none",
        "slots": extract_slots(text),
        "rewritten_query": message,
        "confidence": confidence,
        "reason": "доменный классификатор: need_search + section",
        "engine": engine,
    }
