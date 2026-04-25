import os
import json
from typing import Dict, Any
from file_tools import identify_main_document


def _archive_content_for_llm(archive_content: Dict[str, Any]) -> Dict[str, Any]:
    """
    Возвращает копию archive_content без поля 'path' в файлах.
    Абсолютные пути вроде /tmp/tmpXXXX/book.pdf бесполезны для LLM,
    засоряют контекст и раскрывают структуру системы.
    """
    return {
        'files': [
            {k: v for k, v in f.items() if k != 'path'}
            for f in archive_content.get('files', [])
        ],
        'metadata_content': archive_content.get('metadata_content', {}),
    }


def _is_uninformative_name(name: str) -> bool:
    """
    True если имя файла/архива не несёт смысловой информации:
    числа, короткие коды, случайный набор символов.
    """
    stem = os.path.splitext(name)[0]
    if stem.isdigit():
        return True
    if len(stem) < 4:
        return True
    # Длинная строка без пробелов/подчёркиваний только из ASCII — вероятно код
    if len(stem) > 15 and ' ' not in stem and '_' not in stem and stem.isascii():
        return True
    return False


def build_initial_prompt(archive_name: str, archive_content: Dict[str, Any]) -> str:
    """Строит первоначальный промпт для LLM"""
    main_doc = identify_main_document(archive_content['files'])
    archive_ext = os.path.splitext(archive_name)[1]

    metadata_files = archive_content.get('metadata_content', {})
    if metadata_files:
        meta_note = f"\nМетафайлы в архиве ({', '.join(metadata_files.keys())}):\n"
        meta_note += "\n---\n".join(
            f"{name}:\n{text}" for name, text in metadata_files.items()
        )
    else:
        meta_note = ""

    llm_content = _archive_content_for_llm(archive_content)

    archive_name_uninformative = _is_uninformative_name(archive_name)
    main_doc_uninformative = _is_uninformative_name(main_doc) if main_doc else True

    if archive_name_uninformative and main_doc_uninformative:
        hint = (
            "ВНИМАНИЕ: Имя архива и имя файла внутри — случайные числа/коды, "
            "они НЕ несут информации о содержимом. "
            "Ты ОБЯЗАН запросить текст из файла (need_more_data). "
            "Переименование на основе имени файла ЗАПРЕЩЕНО."
        )
    elif archive_name_uninformative:
        hint = (
            "ВНИМАНИЕ: Имя архива является случайным кодом. "
            "Если имя файла внутри тоже не раскрывает содержимое — запроси текст."
        )
    else:
        hint = (
            "Используй имя архива как подсказку, но убедись что оно действительно "
            "описывает содержимое, а не является техническим кодом."
        )

    prompt = f"""Твоя задача — определить название книги или документа в архиве и предложить информативное имя для архива.

Исходное имя архива: "{archive_name}"
Расширение архива: "{archive_ext}"
Основной документ внутри: "{main_doc or 'Не определен'}"
{meta_note}
Содержимое архива:
{json.dumps(llm_content, ensure_ascii=False, indent=2)}

{hint}

Правила принятия решения:
- Имя считается ИНФОРМАТИВНЫМ только если явно указывает на название книги, автора или тему.
- Числа, коды, артикулы, случайные буквы — это НЕ информация о содержимом.
- Имя файла внутри архива (например "076510.pdf") — это НЕ название книги.
- Если есть хоть малейшие сомнения — запрашивай текст (need_more_data).

Верни JSON с одним из двух вариантов:

1. Только если название книги ОДНОЗНАЧНО известно:
{{"decision": "rename", "new_name": "Автор - Название{archive_ext}"}}

2. Во всех остальных случаях:
{{"decision": "need_more_data", "action": "extract_text", "target": "{main_doc}", "parameters": {{"type": "first_chars", "amount": 2000}}}}

Язык имени файла: используй язык оригинала. Если книга на русском — имя на кириллице. Если на английском — латиница. Транслитерация не нужна.
Формат: "Автор - Название{archive_ext}" или "Название{archive_ext}".
Расширение нового имени СТРОГО {archive_ext} — это расширение архива, а не файла внутри (.djvu/.pdf и т.п.).
В поле "target" указывай конкретное имя файла из списка выше."""
    return prompt


def build_retry_prompt(archive_path: str, archive_content: Dict[str, Any],
                       rejected_name: str, target_file: str,
                       extracted_text: str) -> str:
    """
    Промпт для повторного запроса после того, как пользователь отверг предложенное имя.
    Сообщает модели что предыдущий вариант не подошёл и передаёт новый текст.
    """
    archive_ext  = os.path.splitext(archive_path)[1]
    preview_text = extracted_text[:3000]

    prompt = f"""Пользователь отклонил предложенное тобой имя архива.

Отклонённый вариант: "{rejected_name}"
Причина отклонения: пользователь сказал, что это имя не отражает содержимое книги.

Архив: {os.path.basename(archive_path)}
Расширение: {archive_ext}
Файл внутри: {target_file}

Дополнительный текст из файла:
{preview_text}

На основе этого текста предложи ДРУГОЕ, более точное имя архива.
Не повторяй отклонённый вариант.

Верни JSON:
{{"decision": "rename", "new_name": "Автор - Название{archive_ext}"}}

Язык имени файла: используй язык оригинала. Если книга на русском — имя на кириллице. Если на английском — латиница. Транслитерация не нужна.
Формат: "Автор - Название{archive_ext}" или "Название{archive_ext}".
Расширение архива СТРОГО {archive_ext} — не .djvu, не .pdf, не любое другое."""
    return prompt


def build_text_analysis_prompt(archive_path: str, archive_content: Dict[str, Any],
                                target_file: str, extracted_text: str) -> str:
    """Строит промпт для анализа извлечённого текста"""
    archive_ext = os.path.splitext(archive_path)[1]

    # Ограничиваем объём текста — больше 2000 символов модели не нужно для названия
    preview_text = extracted_text[:2000]
    # Не заменяем кавычки и спецсимволы: это портит кириллические тексты и названия книг

    prompt = f"""Проанализируй текст из файла внутри архива и предложи подходящее имя для АРХИВА.

Архив: {os.path.basename(archive_path)}
Расширение архива: {archive_ext}
Файл внутри архива: {target_file}
Извлечено текста: {len(extracted_text)} символов

Текст:
{preview_text}

Верни JSON ответ:
{{"decision": "rename", "new_name": "Предлагаемое_имя_архива{archive_ext}"}}

ВАЖНО:
- Имя должно отражать содержание: автор и/или название книги
- Расширение нового имени СТРОГО {archive_ext} (это расширение архива, не файла внутри)
- Язык имени файла: используй язык оригинала. Если книга на русском — имя на кириллице. Если на английском — латиница. Транслитерация не нужна."""
    return prompt


def build_categorize_prompt(book_name: str, extracted_text: str,
                             categories: list) -> str:
    """
    Промпт для определения тематической категории книги.
    Возвращает JSON: {"category": "Название категории"}
    """
    cats = "\n".join(f"- {c}" for c in categories)
    preview = extracted_text[:1000] if extracted_text else ""

    return f"""Определи тематическую категорию книги из списка ниже.

Название книги: "{book_name}"
Фрагмент текста:
{preview}

Доступные категории:
{cats}

Выбери ОДНУ наиболее подходящую категорию из списка выше.
Если тема не совпадает ни с одной — выбери последнюю в списке.

Верни только JSON:
{{"category": "Точное название категории из списка"}}"""
