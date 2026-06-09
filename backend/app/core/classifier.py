from __future__ import annotations

import re
from typing import Any


GREETINGS = {"/start", "привет", "здравствуйте", "добрый день", "добрый вечер"}

GENERAL_OFFER_PATTERNS = [
    "что вы предлагаете",
    "что предлагаете",
    "что продаете",
    "что продаёте",
    "что есть",
    "какие товары",
    "какой ассортимент",
    "ассортимент",
    "каталог",
    "чем занимаетесь",
    "есть изделия",
    "есть товары",
]

SERVICE_MARKERS = {
    "delivery": ["доставка", "доставить", "самовывоз", "привезти", "курьер"],
    "contacts": ["адрес", "адреса", "контакт", "контакты", "телефон", "где находитесь", "где вы находитесь", "как доехать", "магазин"],
    "tinting": ["колеровка", "колеровать", "подбор цвета", "подобрать цвет", "оттенок", "палитра"],
    "order": ["заказ", "купить", "оформить", "оплата", "наличие", "есть в наличии"],
    "brands": ["бренд", "бренды", "марки", "производители"],
    "partners": ["дизайнер", "строитель", "партнер", "партнёр", "сотрудничество"],
}

CATEGORY_MARKERS = {
    "interior_paint": ["интерьер", "стены", "стена", "потолок", "кухня", "ванн", "детская", "гостиная", "моющаяся"],
    "facade_paint": ["фасад", "наруж", "улица", "уличная"],
    "metal_enamel": ["эмаль", "металл", "металлу", "антикор", "молотков"],
    "wood_paint": ["дерево", "дереву", "древес", "мебель", "окна", "двери", "пропитка", "антисептик"],
    "primer": ["грунт", "грунтовка"],
    "lacquer_oil": ["лак", "масло", "воск"],
    "decorative": ["декоратив", "фактур", "штукатурка", "декор"],
    "glue_sealant": ["клей", "герметик", "пена", "жидкие гвозди"],
    "tools": ["валик", "кисть", "шпатель", "лента", "ведро", "ванночка", "ванна для краски", "лоток"],
    "wallpaper_decor": ["обои", "лепнина", "карниз", "молдинг", "панель"],
}

CATEGORY_TEXT = {
    "interior_paint": "интерьерные краски для стен потолка кухни ванной детской моющаяся матовая",
    "facade_paint": "фасадные краски для наружных работ улицы минеральной поверхности",
    "metal_enamel": "эмали и краски по металлу антикоррозионные белые цветные",
    "wood_paint": "краски пропитки антисептики лаки для дерева мебели окон дверей",
    "primer": "грунтовки грунт подготовка поверхности перед окрашиванием",
    "lacquer_oil": "лаки масла воски защитные покрытия",
    "decorative": "декоративные покрытия фактурные краски штукатурки",
    "glue_sealant": "клеи герметики монтажные пены",
    "tools": "малярные инструменты валики кисти шпатели ленты ванночки",
    "wallpaper_decor": "обои под покраску декоративная лепнина панели карнизы молдинги",
}

ROOM_MARKERS = {
    "ванная": ["ванная", "ванной", "санузел", "влажная зона", "мокрая зона"],
    "кухня": ["кухня", "кухни"],
    "детская": ["детская", "детской"],
    "гостиная": ["гостиная", "зал"],
    "потолок": ["потолок", "потолка"],
    "фасад": ["фасад"],
}

SURFACE_MARKERS = {
    "металл": ["металл", "металлу", "радиатор", "труба"],
    "дерево": ["дерево", "дереву", "древес", "мебель", "окно", "дверь"],
    "минеральная поверхность": ["бетон", "штукатурка", "кирпич", "минеральн"],
    "пластик": ["пластик"],
    "кафель": ["кафель", "плитка"],
    "обои": ["обои"],
}

FINISH_MARKERS = {
    "матовая": ["матовая", "матовый", "глубокоматовая", "глубокоматовый"],
    "полуматовая": ["полуматовая", "полуматовый"],
    "глянцевая": ["глянцевая", "глянцевый"],
    "полуглянцевая": ["полуглянцевая", "полуглянцевый"],
}

BRAND_MARKERS = ["dulux", "marshall", "hammerite", "pinotex", "tikkurila", "oikos", "dufa", "kudo", "argile", "storch", "anza"]


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower().replace("ё", "е")).strip()


def contains_any(text: str, markers: list[str]) -> bool:
    return any(marker in text for marker in markers)


def first_slot(text: str, mapping: dict[str, list[str]]) -> str | None:
    for value, markers in mapping.items():
        if contains_any(text, markers):
            return value
    return None


def extract_slots(text: str) -> dict[str, Any]:
    slots: dict[str, Any] = {}
    category = first_slot(text, CATEGORY_MARKERS)
    if any(marker in text for marker in ["ванночка", "ванна для краски", "лоток"]):
        category = "tools"
    room = first_slot(text, ROOM_MARKERS)
    surface = first_slot(text, SURFACE_MARKERS)
    finish = first_slot(text, FINISH_MARKERS)
    brand = next((brand for brand in BRAND_MARKERS if brand in text), None)
    volume_match = re.search(r"\b\d+(?:[,.]\d+)?\s*(?:л|литр|кг|мл)\b", text)
    if category:
        slots["category"] = category
    if room:
        slots["room"] = room
    if surface:
        slots["surface"] = surface
    if finish:
        slots["finish"] = finish
    if brand:
        slots["brand"] = brand
    if volume_match:
        slots["volume"] = volume_match.group(0)
    if any(marker in text for marker in ["цена", "стоимость", "сколько стоит"]):
        slots["price"] = True
    if any(marker in text for marker in ["наличие", "в наличии", "есть ли"]):
        slots["availability"] = True
    if any(marker in text for marker in ["белая", "белый", "white"]):
        slots["color"] = "белая"
    return slots


def rewritten_query(message: str, intent: str, slots: dict[str, Any]) -> str:
    if intent == "general_offer":
        return (
            "что предлагает Центр красок ассортимент каталог товары "
            "интерьерные фасадные краски эмали грунтовки лаки масла декоративные покрытия "
            "малярные инструменты обои лепнина колеровка"
        )
    if intent in {"delivery", "contacts", "tinting", "order", "brands", "partners"}:
        service_text = {
            "delivery": "доставка самовывоз условия доставки Центр красок",
            "contacts": "адреса контакты телефоны магазины Центр красок",
            "tinting": "колеровка подбор цвета краски оттенок Центр красок",
            "order": "как купить оформить заказ оплата наличие Центр красок",
            "brands": "какие бренды представлены Центр красок Dulux Marshall Hammerite Pinotex",
            "partners": "условия для дизайнеров строителей партнеров Центр красок",
        }
        return service_text[intent]
    parts: list[str] = []
    category = slots.get("category")
    if category:
        parts.append(CATEGORY_TEXT.get(str(category), str(category)))
    for key in ["brand", "room", "surface", "finish", "color", "volume"]:
        value = slots.get(key)
        if value:
            parts.append(str(value))
    if slots.get("price"):
        parts.append("цена стоимость")
    if slots.get("availability"):
        parts.append("наличие")
    parts.append(message)
    return normalize(" ".join(parts))


def classify_rule(message: str, history: list[dict[str, str]] | None = None, engine: str = "domain_rules") -> dict[str, Any]:
    text = normalize(message)
    if text in GREETINGS:
        return {
            "need_search": False,
            "need_rewrite": False,
            "query_type": "greeting",
            "intent": "greeting",
            "search_scope": "none",
            "slots": {},
            "rewritten_query": message,
            "confidence": 1.0,
            "reason": "приветствие",
            "engine": engine,
        }

    slots = extract_slots(text)
    intent = "unclear"
    scope = "mixed"
    confidence = 0.45

    if contains_any(text, GENERAL_OFFER_PATTERNS):
        intent, scope, confidence = "general_offer", "guides", 0.95
    else:
        for service_intent, markers in SERVICE_MARKERS.items():
            if contains_any(text, markers):
                intent = service_intent
                scope = "service"
                confidence = 0.9
                break

    if intent == "unclear":
        if slots.get("category") == "tools":
            intent, scope, confidence = "category_browse", "products", 0.82
        elif slots or any(word in text for word in ["краска", "краски", "эмаль", "товар", "изделие"]):
            has_exact_product_hint = bool(slots.get("brand") or slots.get("volume") or slots.get("price") or slots.get("availability"))
            intent = "product_lookup" if has_exact_product_hint else "product_recommendation"
            scope = "products" if has_exact_product_hint else "mixed"
            confidence = 0.78 if has_exact_product_hint else 0.72

    question_markers = ["как", "что", "где", "когда", "какой", "какая", "какие", "сколько", "можно", "нужно"]
    need_search = intent not in {"greeting"} and (
        intent != "unclear" or contains_any(text, question_markers) or len(text.split()) >= 4
    )
    if intent == "unclear" and not need_search:
        scope = "none"

    return {
        "need_search": need_search,
        "need_rewrite": False,
        "query_type": "knowledge_base" if need_search else "general_chat",
        "intent": intent if need_search else "general_chat",
        "search_scope": scope if need_search else "none",
        "slots": slots,
        "rewritten_query": rewritten_query(message, intent, slots) if need_search else message,
        "confidence": confidence,
        "reason": "доменный классификатор Центр красок",
        "engine": engine,
    }
