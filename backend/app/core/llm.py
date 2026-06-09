import json
import logging
import os
import re
from time import perf_counter
from typing import Any

from openai import OpenAI

XAI_API_KEY = os.environ.get("XAI_API_KEY", "")
XAI_BASE_URL = os.environ.get("XAI_BASE_URL", "https://api.x.ai/v1")
XAI_MODEL = os.environ.get("XAI_MODEL", "grok-4.3")

logger = logging.getLogger(__name__)


def has_llm() -> bool:
    return bool(XAI_API_KEY)


def client() -> OpenAI:
    return OpenAI(api_key=XAI_API_KEY, base_url=XAI_BASE_URL)


def chat(messages: list[dict[str, str]], temperature: float = 0.2, response_format: dict[str, Any] | None = None) -> str:
    if not XAI_API_KEY:
        raise RuntimeError("XAI_API_KEY is not configured")
    kwargs: dict[str, Any] = {"model": XAI_MODEL, "messages": messages, "temperature": temperature}
    if response_format:
        kwargs["response_format"] = response_format
    response = client().chat.completions.create(**kwargs)
    return response.choices[0].message.content or ""


def parse_json(text: str) -> dict[str, Any]:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            raise
        return json.loads(match.group(0))


def rule_classify(message: str) -> dict[str, Any]:
    lowered = message.lower().strip()
    if lowered in {"/start", "привет", "здравствуйте", "добрый день"}:
        return {"need_search": False, "need_rewrite": False, "query_type": "greeting", "reason": "приветствие"}
    markers = [
        "я хочу",
        "мне нужно",
        "как",
        "что",
        "почему",
        "сколько",
        "где",
        "когда",
        "какой",
        "какая",
        "какие",
        "можно",
        "нужно ли",
        "как сделать",
    ]
    need_search = any(marker in lowered for marker in markers) or len(lowered.split()) >= 4
    return {
        "need_search": need_search,
        "need_rewrite": False,
        "query_type": "knowledge_base" if need_search else "general_chat",
        "reason": "правило fallback",
    }


def classify_message(message: str, history: list[dict[str, str]], request_id: str = "-") -> dict[str, Any]:
    started = perf_counter()
    if not has_llm():
        result = rule_classify(message)
        logger.info(
            "request_id=%s stage=classifier event=fallback_result need_search=%s query_type=%s duration_ms=%s",
            request_id,
            result["need_search"],
            result["query_type"],
            round((perf_counter() - started) * 1000),
        )
        return result
    history_text = "\n".join(f"{item['role']}: {item['content']}" for item in history[-6:])
    prompt = f"""
Ты классификатор сообщений Telegram RAG-бота. Отвечай только на русском.
Реши, нужно ли искать ответ в базе знаний.

История:
{history_text or "(пусто)"}

Сообщение:
{message}

Верни только JSON:
{{"need_search": true, "need_rewrite": true, "query_type": "knowledge_base | general_chat | greeting | unclear", "reason": "короткая причина"}}
"""
    try:
        logger.info("request_id=%s stage=classifier event=llm_request model=%s history=%s", request_id, XAI_MODEL, len(history))
        result = parse_json(chat([
            {"role": "system", "content": "Верни только валидный JSON."},
            {"role": "user", "content": prompt},
        ], temperature=0, response_format={"type": "json_object"}))
        classified = {
            "need_search": bool(result.get("need_search")),
            "need_rewrite": bool(result.get("need_rewrite")),
            "query_type": str(result.get("query_type") or "unclear"),
            "reason": str(result.get("reason") or ""),
        }
        logger.info(
            "request_id=%s stage=classifier event=llm_result need_search=%s need_rewrite=%s query_type=%s duration_ms=%s",
            request_id,
            classified["need_search"],
            classified["need_rewrite"],
            classified["query_type"],
            round((perf_counter() - started) * 1000),
        )
        return classified
    except Exception as exc:
        fallback = rule_classify(message)
        fallback["reason"] += f"; LLM classifier failed: {exc}"
        logger.exception("request_id=%s stage=classifier event=llm_failed fallback=%s error=%s", request_id, fallback, exc)
        return fallback


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
        rewritten = chat([
            {"role": "system", "content": "Ты переписываешь вопросы для поиска."},
            {"role": "user", "content": prompt},
        ], temperature=0).strip().strip('"')
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
        return "Сейчас я отвечаю только по базе знаний. Задайте вопрос по вашим данным."
    started = perf_counter()
    messages = [{"role": "system", "content": "Ты русский Telegram-ассистент. Отвечай только на русском."}]
    messages.extend(history[-6:])
    messages.append({"role": "user", "content": message})
    answer = chat(messages, temperature=0.3).strip()
    logger.info("request_id=%s stage=answer event=direct_llm_done answer_len=%s duration_ms=%s", request_id, len(answer), round((perf_counter() - started) * 1000))
    return answer


def grounded_answer(question: str, candidates: list[dict[str, Any]], history: list[dict[str, str]], request_id: str = "-") -> str:
    if not candidates:
        logger.info("request_id=%s stage=answer event=grounded_no_candidates", request_id)
        return "В базе знаний не найдено достаточно информации по этому вопросу."
    if not has_llm():
        logger.info("request_id=%s stage=answer event=grounded_fallback reason=no_llm candidate_id=%s", request_id, candidates[0].get("id"))
        return candidates[0].get("answer") or "Ответ найден, но текст ответа пустой."
    context = "\n\n".join(f"[{i}] Вопрос: {c['question']}\nОтвет: {c['answer']}" for i, c in enumerate(candidates[:5], start=1))
    prompt = f"""
Ответь пользователю только по найденным фрагментам базы. Не выдумывай факты.
Если ответа недостаточно, скажи: "В базе знаний не найдено достаточно информации по этому вопросу."

Фрагменты:
{context}

Вопрос пользователя:
{question}
"""
    started = perf_counter()
    answer = chat([
        {"role": "system", "content": "Ты RAG-ассистент. Отвечай только на русском."},
        {"role": "user", "content": prompt},
    ], temperature=0.2).strip()
    logger.info("request_id=%s stage=answer event=grounded_llm_done candidates=%s answer_len=%s duration_ms=%s", request_id, len(candidates), len(answer), round((perf_counter() - started) * 1000))
    return answer
