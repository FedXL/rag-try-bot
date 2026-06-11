import json
import logging
import os
import re
from time import perf_counter
from typing import Any

from openai import OpenAI

from app.content.models import ClassifierClass

from .classifier import classify_rule
from .models import LLMRequestLog

XAI_API_KEY = os.environ.get("XAI_API_KEY", "")
XAI_BASE_URL = os.environ.get("XAI_BASE_URL", "https://api.x.ai/v1")
XAI_MODEL = os.environ.get("XAI_MODEL", "grok-4.3")

logger = logging.getLogger(__name__)


def has_llm() -> bool:
    return bool(XAI_API_KEY)


def client() -> OpenAI:
    return OpenAI(api_key=XAI_API_KEY, base_url=XAI_BASE_URL)


def safe_log_llm(payload: dict[str, Any]) -> None:
    try:
        LLMRequestLog.objects.create(**payload)
    except Exception as exc:
        logger.warning("request_id=%s stage=llm_log event=write_failed error=%s", payload.get("request_id"), exc)


def response_metadata(response: Any) -> dict[str, Any]:
    usage = getattr(response, "usage", None)
    return {
        "id": getattr(response, "id", ""),
        "created": getattr(response, "created", None),
        "usage": usage.model_dump(mode="json") if hasattr(usage, "model_dump") else {},
    }


def chat(
    messages: list[dict[str, str]],
    temperature: float = 0.2,
    response_format: dict[str, Any] | None = None,
    request_id: str = "-",
    purpose: str = "chat",
) -> str:
    if not XAI_API_KEY:
        raise RuntimeError("XAI_API_KEY is not configured")
    started = perf_counter()
    kwargs: dict[str, Any] = {"model": XAI_MODEL, "messages": messages, "temperature": temperature}
    if response_format:
        kwargs["response_format"] = response_format
    try:
        response = client().chat.completions.create(**kwargs)
        content = response.choices[0].message.content or ""
        safe_log_llm(
            {
                "request_id": request_id,
                "purpose": purpose,
                "provider": "xai",
                "model": XAI_MODEL,
                "temperature": temperature,
                "request_messages": messages,
                "request_payload": {"response_format": response_format or {}},
                "response_text": content,
                "response_payload": response_metadata(response),
                "status": "success",
                "duration_ms": round((perf_counter() - started) * 1000),
            }
        )
        return content
    except Exception as exc:
        safe_log_llm(
            {
                "request_id": request_id,
                "purpose": purpose,
                "provider": "xai",
                "model": XAI_MODEL,
                "temperature": temperature,
                "request_messages": messages,
                "request_payload": {"response_format": response_format or {}},
                "status": "error",
                "error": str(exc),
                "duration_ms": round((perf_counter() - started) * 1000),
            }
        )
        raise


def parse_json(text: str) -> dict[str, Any]:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            raise
        return json.loads(match.group(0))


def active_classifier_classes() -> list[ClassifierClass]:
    return list(ClassifierClass.objects.filter(is_active=True).order_by("slug"))


def active_classifier_slugs() -> set[str]:
    return set(ClassifierClass.objects.filter(is_active=True).values_list("slug", flat=True))


def allowed_class_slugs(classes: list[ClassifierClass]) -> set[str]:
    return {item.slug for item in classes}


def normalize_section(value: Any, fallback: str = "mixed", allowed_slugs: set[str] | None = None) -> str:
    section = str(value or fallback).strip()
    if section in {"brands", "catalog"}:
        section = "product"
    if allowed_slugs:
        return section if section in allowed_slugs else fallback
    return section


def should_bypass_llm(rule_result: dict[str, Any]) -> bool:
    return rule_result.get("query_type") in {"greeting", "general_chat"} and rule_result.get("class_slug") == "none"


def enforce_active_class(result: dict[str, Any]) -> dict[str, Any]:
    class_slug = str(result.get("class_slug") or result.get("section") or "none")
    if class_slug in {"", "none"}:
        return result
    active_slugs = active_classifier_slugs()
    if class_slug in active_slugs:
        return result
    fallback = "mixed" if "mixed" in active_slugs else "none"
    cleaned = dict(result)
    cleaned["need_search"] = fallback != "none"
    cleaned["class_slug"] = fallback
    cleaned["section"] = fallback
    cleaned["search_scope"] = fallback
    cleaned["intent"] = "inactive_class"
    cleaned["reason"] = f"{result.get('reason') or ''}; class {class_slug} is inactive".strip("; ")
    return cleaned


def format_classifier_classes(classes: list[ClassifierClass]) -> str:
    rows: list[str] = []
    for item in classes:
        rows.append(
            "\n".join(
                [
                    f"- {item.slug}",
                    f"  title: {item.title}",
                    f"  kind: {item.kind}",
                    f"  description: {item.description or '-'}",
                ]
            )
        )
    return "\n".join(rows)


def build_classifier_prompt(message: str, history: list[dict[str, str]], classes: list[ClassifierClass]) -> str:
    history_text = "\n".join(f"{item['role']}: {item['content']}" for item in history[-6:])
    class_slugs = " | ".join(item.slug for item in classes)
    allowed_slugs = allowed_class_slugs(classes)
    rule_rows: list[str] = []
    example_rows: list[str] = []

    if "contacts" in allowed_slugs:
        rule_rows.append(
            "- contacts: адреса, телефоны, режим работы, где находится магазин, где забрать заказ или товар, "
            "самовывоз как место получения, город Алматы/Астана."
        )
        example_rows.append(
            '- "А где забрать краску в Алматы" -> contacts, потому что пользователь спрашивает место получения/адрес в городе.'
        )
    if "product" in allowed_slugs:
        rule_rows.append(
            "- product: цена, наличие, остатки, артикул, SKU, конкретный товар, бренд, категория товара, подбор товара, "
            "сравнение товаров/брендов, расчет бюджета по товарам."
        )
        example_rows.extend(
            [
                '- "есть краска Dulux в Алматы" -> product, потому что пользователь спрашивает наличие товара.',
                '- "сколько стоит артикул 5811071" -> product, потому что пользователь спрашивает цену конкретного товара.',
            ]
        )
    if "help" in allowed_slugs:
        rule_rows.append(
            "- help: правила доставки, оплаты, возврата, гарантии, как оформить заказ, условия сервиса."
        )
        example_rows.append(
            '- "как оформить доставку" -> help, потому что пользователь спрашивает правило/процесс доставки.'
        )
    if "none" in allowed_slugs:
        rule_rows.append("- none: приветствие, small talk или сообщение, где не нужен поиск по базе.")
    if "mixed" in allowed_slugs:
        rule_rows.append(
            "- mixed: только если вопрос реально относится к нескольким классам и без уточнения нельзя выбрать один."
        )

    classifier_rules = "\n".join(rule_rows) or "- Выбирай только class_slug из списка активных классов."
    classifier_examples = "\n".join(example_rows) or "- Используй описания активных классов как источник правил выбора."
    return f"""
Ты классификатор Telegram-бота магазина "Центр Красок".

Задача:
1. Определи, нужно ли отвечать на сообщение через базу данных.
2. Если база нужна, выбери ровно один class_slug из списка активных классов.
3. Если база не нужна, выбери class_slug="none".
4. Если вопрос относится к нескольким классам или невозможно выбрать один класс, выбери class_slug="mixed".

Используй историю только для восстановления контекста текущего вопроса.
Не отвечай пользователю. Верни только JSON.

Активные классы:
{format_classifier_classes(classes)}

Правила выбора:
{classifier_rules}

Примеры:
{classifier_examples}

История:
{history_text or "(пусто)"}

Сообщение пользователя:
{message}

Верни JSON строго в таком формате:
{{
  "need_search": true,
  "need_rewrite": false,
  "query_type": "knowledge_base | general_chat | greeting | unclear",
  "class_slug": "{class_slugs}",
  "section": "тот же class_slug",
  "intent": "короткий машинный intent",
  "slots": {{}},
  "rewritten_query": "самостоятельный поисковый запрос для выбранного класса",
  "confidence": 0.0,
  "reason": "короткая причина выбора класса"
}}
"""


def classify_message(message: str, history: list[dict[str, str]], request_id: str = "-") -> dict[str, Any]:
    started = perf_counter()
    rule_result = classify_rule(message, history, engine="domain_rules")
    if should_bypass_llm(rule_result):
        logger.info(
            "request_id=%s stage=classifier event=rule_bypass need_search=%s query_type=%s section=%s intent=%s confidence=%s duration_ms=%s",
            request_id,
            rule_result.get("need_search"),
            rule_result.get("query_type"),
            rule_result.get("section"),
            rule_result.get("intent"),
            rule_result.get("confidence"),
            round((perf_counter() - started) * 1000),
        )
        return enforce_active_class(rule_result)

    if not has_llm():
        logger.info(
            "request_id=%s stage=classifier event=rule_fallback reason=no_llm need_search=%s query_type=%s section=%s intent=%s confidence=%s duration_ms=%s",
            request_id,
            rule_result.get("need_search"),
            rule_result.get("query_type"),
            rule_result.get("section"),
            rule_result.get("intent"),
            rule_result.get("confidence"),
            round((perf_counter() - started) * 1000),
        )
        return enforce_active_class(rule_result)

    classes = active_classifier_classes()
    allowed_slugs = allowed_class_slugs(classes)
    if not classes:
        logger.warning("request_id=%s stage=classifier event=rule_fallback reason=no_active_classes", request_id)
        return enforce_active_class(rule_result)

    prompt = build_classifier_prompt(message, history, classes)
    try:
        logger.info("request_id=%s stage=classifier event=llm_request model=%s history=%s classes=%s", request_id, XAI_MODEL, len(history), len(classes))
        result = parse_json(
            chat(
                [
                    {"role": "system", "content": "Верни только валидный JSON без markdown."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0,
                response_format={"type": "json_object"},
                request_id=request_id,
                purpose="classifier",
            )
        )
        need_search = bool(result.get("need_search"))
        fallback = "mixed" if "mixed" in allowed_slugs else next(iter(sorted(allowed_slugs)))
        section = normalize_section(result.get("class_slug") or result.get("section"), fallback, allowed_slugs)
        if not need_search:
            section = "none" if "none" in allowed_slugs else fallback
        classified = {
            "need_search": need_search,
            "need_rewrite": bool(result.get("need_rewrite")),
            "query_type": str(result.get("query_type") or ("knowledge_base" if need_search else "general_chat")),
            "section": section,
            "class_slug": section,
            "intent": str(result.get("intent") or rule_result.get("intent") or "unclear"),
            "search_scope": section,
            "slots": result.get("slots") if isinstance(result.get("slots"), dict) else rule_result.get("slots", {}),
            "rewritten_query": str(result.get("rewritten_query") or rule_result.get("rewritten_query") or message),
            "confidence": float(result.get("confidence") or 0.65),
            "reason": str(result.get("reason") or ""),
            "engine": "llm",
        }
        logger.info(
            "request_id=%s stage=classifier event=llm_result need_search=%s need_rewrite=%s query_type=%s section=%s intent=%s duration_ms=%s",
            request_id,
            classified["need_search"],
            classified["need_rewrite"],
            classified["query_type"],
            classified["section"],
            classified["intent"],
            round((perf_counter() - started) * 1000),
        )
        return classified
    except Exception as exc:
        fallback = classify_rule(message, history, engine="domain_rules_after_llm_error")
        fallback["reason"] += f"; LLM classifier failed: {exc}"
        logger.exception("request_id=%s stage=classifier event=llm_failed fallback=%s error=%s", request_id, fallback, exc)
        return enforce_active_class(fallback)


def rewrite_query(message: str, history: list[dict[str, str]], request_id: str = "-") -> str:
    if not has_llm():
        logger.info("request_id=%s stage=query_rewrite event=skipped reason=no_llm", request_id)
        return message
    history_text = "\n".join(f"{item['role']}: {item['content']}" for item in history[-6:])
    prompt = f"""
Перепиши сообщение в самостоятельный поисковый запрос для базы знаний.
Не отвечай, верни только запрос.

История:
{history_text or "(пусто)"}

Сообщение:
{message}
"""
    try:
        started = perf_counter()
        rewritten = chat(
            [
                {"role": "system", "content": "Ты переписываешь вопросы для поиска. Отвечай только на русском."},
                {"role": "user", "content": prompt},
            ],
            temperature=0,
            request_id=request_id,
            purpose="query_rewrite",
        ).strip().strip('"')
        logger.info("request_id=%s stage=query_rewrite event=done query_len=%s duration_ms=%s", request_id, len(rewritten), round((perf_counter() - started) * 1000))
        return rewritten
    except Exception as exc:
        logger.exception("request_id=%s stage=query_rewrite event=failed error=%s", request_id, exc)
        return message


def direct_answer(message: str, history: list[dict[str, str]], request_id: str = "-") -> str:
    if message.strip() == "/start":
        logger.info("request_id=%s stage=answer event=start_message", request_id)
        return "Здравствуйте. Задайте вопрос, и я найду ответ в базе знаний."
    if not has_llm():
        logger.info("request_id=%s stage=answer event=direct_fallback reason=no_llm", request_id)
        return "Сейчас я отвечаю только по базе знаний. Задайте вопрос по данным магазина."
    started = perf_counter()
    messages = [{"role": "system", "content": "Ты русский Telegram-ассистент магазина. Отвечай только на русском."}]
    messages.extend(history[-6:])
    messages.append({"role": "user", "content": message})
    answer = chat(messages, temperature=0.3, request_id=request_id, purpose="direct_answer").strip()
    logger.info("request_id=%s stage=answer event=direct_llm_done answer_len=%s duration_ms=%s", request_id, len(answer), round((perf_counter() - started) * 1000))
    return answer


def grounded_answer(question: str, candidates: list[dict[str, Any]], history: list[dict[str, str]], request_id: str = "-") -> str:
    if not candidates:
        logger.info("request_id=%s stage=answer event=grounded_no_candidates", request_id)
        return "В базе знаний не найдено достаточно информации по этому вопросу."
    if not has_llm():
        logger.info("request_id=%s stage=answer event=grounded_fallback reason=no_llm candidate_id=%s", request_id, candidates[0].get("id"))
        return candidates[0].get("answer") or "Ответ найден, но текст ответа пустой."

    context = "\n\n".join(
        f"[{index}] Тип: {candidate.get('content_type') or 'article'}\n"
        f"Раздел: {candidate.get('class_slug') or candidate.get('section') or '-'}\n"
        f"Заголовок: {candidate.get('question') or candidate.get('title') or '-'}\n"
        f"Фрагмент: {candidate.get('answer') or candidate.get('chunk_text') or ''}"
        for index, candidate in enumerate(candidates[:5], start=1)
    )
    prompt = f"""
Ответь пользователю только по найденным фрагментам базы. Не выдумывай факты.
Если ответа недостаточно, скажи: "В базе знаний не найдено достаточно информации по этому вопросу."

Фрагменты:
{context}

Вопрос пользователя:
{question}
"""
    started = perf_counter()
    answer = chat(
        [
            {"role": "system", "content": "Ты RAG-ассистент. Отвечай только на русском."},
            {"role": "user", "content": prompt},
        ],
        temperature=0.2,
        request_id=request_id,
        purpose="grounded_answer",
    ).strip()
    logger.info("request_id=%s stage=answer event=grounded_llm_done candidates=%s answer_len=%s duration_ms=%s", request_id, len(candidates), len(answer), round((perf_counter() - started) * 1000))
    return answer
