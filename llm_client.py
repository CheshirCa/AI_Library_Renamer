import json
import logging
import requests
from config import OLLAMA_BASE_URL, OLLAMA_MODEL, OLLAMA_TIMEOUT

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """Ты должен отвечать ТОЛЬКО в формате JSON, без каких-либо дополнительных объяснений,
комментариев или текста вне JSON.
Твой ответ должен быть валидным JSON объектом с одной из двух структур:

1. Для переименования:
{"decision": "rename", "new_name": "имя_файла.расширение"}

2. Для запроса дополнительных данных:
{"decision": "need_more_data", "action": "extract_text", "target": "конкретное_имя_файла.расширение", "parameters": {"type": "first_chars", "amount": 1000}}

ВАЖНО: В поле "target" всегда указывай конкретное существующее имя файла из структуры архива."""


def send_to_llm(prompt: str) -> str:
    """
    Отправляет промпт в Ollama API и возвращает ответ.
    Совместима с оригинальным интерфейсом: принимает строку, возвращает строку JSON.
    """
    try:
        logger.debug(f"Отправляем запрос к Ollama (модель: {OLLAMA_MODEL})...")

        payload = {
            "model": OLLAMA_MODEL,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user",   "content": prompt},
            ],
            "stream": False,
            "options": {
                "temperature": 0.1,
                "num_predict": 1000,
            },
        }

        response = requests.post(
            f"{OLLAMA_BASE_URL}/api/chat",
            json=payload,
            timeout=OLLAMA_TIMEOUT,
        )
        response.raise_for_status()

        content = response.json()["message"]["content"].strip()
        logger.debug(f"Получен ответ от Ollama: {content[:200]}")

        # Убираем markdown-обёртку если модель всё же добавила её
        content = _strip_markdown_json(content)

        return content

    except requests.exceptions.ConnectionError:
        logger.error("Не удалось подключиться к Ollama. Убедитесь, что сервис запущен: ollama serve")
        return get_fallback_response(prompt)
    except requests.exceptions.Timeout:
        logger.error(f"Ollama не ответила за {OLLAMA_TIMEOUT} секунд. Попробуйте уменьшить объём данных или увеличить OLLAMA_TIMEOUT.")
        return get_fallback_response(prompt)
    except Exception as e:
        logger.error(f"Ошибка при обращении к Ollama API: {e}")
        return get_fallback_response(prompt)


def _strip_markdown_json(text: str) -> str:
    """Убирает ```json ... ``` обёртку если модель её добавила"""
    if text.startswith("```json"):
        text = text[7:]
    elif text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    return text.strip()


def get_fallback_response(prompt: str) -> str:
    """
    Возвращает fallback-ответ на основе анализа промпта.
    Логика сохранена из оригинала.
    """
    prompt_lower = prompt.lower()
    for ext in [".pdf", ".docx", ".fb2", ".djvu", ".epub", ".txt"]:
        if ext in prompt_lower:
            target = f"*{ext}"
            return json.dumps({
                "decision": "need_more_data",
                "action": "extract_text",
                "target": target,
                "parameters": {"type": "first_chars", "amount": 1000},
            }, ensure_ascii=False)

    return json.dumps({
        "decision": "need_more_data",
        "action": "extract_text",
        "target": "document.*",
        "parameters": {"type": "first_chars", "amount": 1000},
    }, ensure_ascii=False)
