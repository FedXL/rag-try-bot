import json
import os
import re
from typing import Any

from openai import OpenAI

XAI_API_KEY = os.environ.get("XAI_API_KEY", "")
XAI_BASE_URL = os.environ.get("XAI_BASE_URL", "https://api.x.ai/v1")
XAI_MODEL = os.environ.get("XAI_MODEL", "grok-4.3")


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


def classify_message(message: str, history: list[dict[str, str]]) -> dict[str, Any]:
    if not has_llm():
        return rule_classify(message)
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
        result = parse_json(chat([
            {"role": "system", "content": "Верни только валидный JSON."},
            {"role": "user", "content": prompt},
        ], temperature=0, response_format={"type": "json_object"}))
        return {
            "need_search": bool(result.get("need_search")),
            "need_rewrite": bool(result.get("need_rewrite")),
            "query_type": str(result.get("query_type") or "unclear"),
            "reason": str(result.get("reason") or ""),
        }
    except Exception as exc:
        fallback = rule_classify(message)
        fallback["reason"] += f"; LLM classifier failed: {exc}"
        return fallback


def rewrite_query(message: str, history: list[dict[str, str]]) -> str:
    if not has_llm():
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
        return chat([
            {"role": "system", "content": "Ты переписываешь вопросы для поиска."},
            {"role": "user", "content": prompt},
        ], temperature=0).strip().strip('"')
    except Exception:
        return message


def direct_answer(message: str, history: list[dict[str, str]]) -> str:
    if message.strip() == "/start":
        return "Здравствуйте. Задайте вопрос, и я найду ответ в базе знаний."
    if not has_llm():
        return "Сейчас я отвечаю только по базе знаний. Задайте вопрос по вашим данным."
    messages = [{"role": "system", "content": "Ты русский Telegram-ассистент. Отвечай только на русском."}]
    messages.extend(history[-6:])
    messages.append({"role": "user", "content": message})
    return chat(messages, temperature=0.3).strip()


def grounded_answer(question: str, candidates: list[dict[str, Any]], history: list[dict[str, str]]) -> str:
    if not candidates:
        return "В базе знаний не найдено достаточно информации по этому вопросу."
    if not has_llm():
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
    return chat([
        {"role": "system", "content": "Ты RAG-ассистент. Отвечай только на русском."},
        {"role": "user", "content": prompt},
    ], temperature=0.2).strip()
