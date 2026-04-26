import os
import json
import collections
from typing import Dict, Any
from file_tools import identify_main_document

# Максимум файлов в промпте — больше не нужно, только нагружает контекст
_MAX_FILES_IN_PROMPT = 30

# Расширения которые считаются «балластом» — группируем, не перечисляем все
_BULK_EXTENSIONS = {'.tif', '.tiff', '.png', '.jpg', '.jpeg', '.bmp',
                    '.gif', '.webp', '.pnm', '.svg'}


def _archive_content_for_llm(archive_content: Dict[str, Any]) -> Dict[str, Any]:
    """
    Подготавливает archive_content для передачи в LLM:
    - Убирает поле 'path' (абсолютные пути бесполезны для модели)
    - Группирует однотипные файлы (сотни TIF → одна строка-сводка)
    - Обрезает список до MAX_FILES_IN_PROMPT значимых файлов
    """
    files = archive_content.get('files', [])

    important = []
    bulk: Dict[str, list] = collections.defaultdict(list)

    for f in files:
        if f.get('type') == 'directory':
            continue
        ext = os.path.splitext(f['name'])[1].lower()
        if ext in _BULK_EXTENSIONS:
            bulk[ext].append(f)
        else:
            important.append({k: v for k, v in f.items() if k != 'path'})

    truncated = len(important) > _MAX_FILES_IN_PROMPT
    shown = important[:_MAX_FILES_IN_PROMPT]

    summaries = []
    for ext, group in sorted(bulk.items()):
        total_size = sum(f.get('size') or 0 for f in group)
        summaries.append({
            'summary': f"{len(group)} файлов {ext} ({total_size // 1024} КБ) — изображения, не анализируются"
        })

    if truncated:
        summaries.append({
            'summary': f"... ещё {len(important) - _MAX_FILES_IN_PROMPT} файлов не показаны"
        })

    return {
        'files': shown + summaries,
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
    if len(stem) > 15 and ' ' not in stem and '_' not in stem and stem.isascii():
        return True
    return False


# ---------------------------------------------------------------------------
# Промпт 1: первичный анализ архива по имени и структуре
# ---------------------------------------------------------------------------

def build_initial_prompt(archive_name: str, archive_content: Dict[str, Any]) -> str:
    """Строит первоначальный промпт для анализа структуры архива."""
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
    main_doc_uninformative     = _is_uninformative_name(main_doc) if main_doc else True

    if archive_name_uninformative and main_doc_uninformative:
        hint = (
            "ВАЖНО: Имя архива и имя файла внутри — цифровые коды или случайный набор символов. "
            "Они не несут информации о содержимом книги. "
            "Переименование по имени файла ЗАПРЕЩЕНО — обязательно запроси текст из документа."
        )
    elif archive_name_uninformative:
        hint = (
            "ВАЖНО: Имя архива выглядит как технический код, а не название книги. "
            "Если имя документа внутри тоже неинформативно — запроси текст."
        )
    else:
        hint = (
            "Имя архива может быть транслитерацией русского названия или содержать аббревиатуры. "
            "Учти это при анализе, но при сомнениях запрашивай текст из документа."
        )

    return f"""Ты — библиограф. Определи, что за книга или документ находится в архиве, \
и предложи корректное имя файла для архива.

Имя архива: «{archive_name}»
Расширение архива: «{archive_ext}»
Основной документ внутри: «{main_doc or 'не определён'}»
{meta_note}
Структура архива:
{json.dumps(llm_content, ensure_ascii=False, indent=2)}

{hint}

Критерии достаточности информации:
— Имя ИНФОРМАТИВНО только если прямо указывает на название книги или автора.
— Числа, артикулы, аббревиатуры без расшифровки, транслит неизвестного происхождения — \
это НЕ название книги.
— Имя файла внутри архива типа «076510.pdf» — НЕ является названием книги.
— При малейших сомнениях — запрашивай текст.

Верни JSON — один из двух вариантов:

Если название книги однозначно известно:
{{"decision": "rename", "new_name": "Автор - Название{archive_ext}"}}

Во всех остальных случаях:
{{"decision": "need_more_data", "action": "extract_text", "target": "{main_doc}", \
"parameters": {{"type": "first_chars", "amount": 2000}}}}

Требования к имени файла:
— Язык оригинала: русская книга → кириллица, английская → латиница. Транслит не нужен.
— Формат: «Автор - Название{archive_ext}» или «Название{archive_ext}».
— Расширение строго {archive_ext} — не .pdf, не .djvu, не расширение файла внутри архива.
— В поле «target» — точное имя файла из списка выше."""


# ---------------------------------------------------------------------------
# Промпт 2: анализ извлечённого текста с OCR-нормализацией
# ---------------------------------------------------------------------------

def build_text_analysis_prompt(archive_path: str, archive_content: Dict[str, Any],
                                target_file: str, extracted_text: str) -> str:
    """
    Промпт для анализа содержимого документа.
    Нормализует OCR-текст, выделяет структурные признаки,
    запрашивает 1–3 варианта имени с обоснованием.
    """
    try:
        from formats.ocr_utils import normalize_ocr_text, extract_ocr_features
        normalized = normalize_ocr_text(extracted_text)
        features   = extract_ocr_features(normalized)
    except ImportError:
        # Старая версия ocr_utils без нормализации — работаем с сырым текстом
        normalized = extracted_text
        features   = ""

    archive_ext    = os.path.splitext(archive_path)[1]
    preview        = normalized[:2500]
    features_block = (
        f"\nВыделенные структурные признаки:\n{features}\n"
        if features else ""
    )

    return f"""Ты — библиограф-специалист по русской и советской литературе. \
Определи точное название книги по фрагменту её текста.

Архив: «{os.path.basename(archive_path)}»  |  расширение: {archive_ext}
Источник текста: «{target_file}»  |  символов после нормализации: {len(normalized)}
{features_block}
Текст документа (OCR, нормализован: переносы склеены, мусорные строки удалены):
<<<
{preview}
>>>

Контекст для анализа:
— Текст может содержать ошибки OCR: замены букв, слипшиеся слова, артефакты сканирования.
— Имена авторов могут быть в формате «Фамилия И.О.», «И. О. Фамилия» или транслитом.
— Советские издания часто содержат: серию, том, год, издательство на титуле.
— Дореформенные буквы (ѣ, і, ъ на конце) указывают на дореволюционное издание.
— Колонтитулы и оглавление — надёжные источники названия и автора.

Рассуждай последовательно:
1. Найди кандидатов на имя автора и название из текста.
2. Оцени достоверность каждого варианта.
3. Верни JSON с 1–3 вариантами от наиболее до наименее вероятного.

Формат ответа — строго JSON, никакого текста вне JSON:
{{
  "decision": "rename",
  "variants": [
    {{"name": "Автор - Название{archive_ext}", "confidence": 90, "reason": "найдено на титульном листе"}},
    {{"name": "Альтернативный вариант{archive_ext}", "confidence": 55, "reason": "возможное прочтение OCR"}}
  ]
}}

Требования:
— Один вариант если уверенность выше 85%, иначе 2–3.
— Кириллица для русских книг, латиница для английских. Транслит недопустим.
— Расширение строго {archive_ext} — не .pdf, не .djvu.
— Формат имени: «Автор - Название{archive_ext}» или «Название{archive_ext}»."""


# ---------------------------------------------------------------------------
# Промпт 3: повторный запрос после отклонения пользователем
# ---------------------------------------------------------------------------

def build_retry_prompt(archive_path: str, archive_content: Dict[str, Any],
                       rejected_name: str, target_file: str,
                       extracted_text: str) -> str:
    """
    Промпт когда пользователь отклонил предложенное имя.
    Передаёт контекст об отклонении и новый текст.
    """
    try:
        from formats.ocr_utils import normalize_ocr_text, extract_ocr_features
        normalized = normalize_ocr_text(extracted_text)
        features   = extract_ocr_features(normalized)
    except ImportError:
        normalized = extracted_text
        features   = ""

    archive_ext    = os.path.splitext(archive_path)[1]
    preview        = normalized[:3000]
    features_block = (
        f"\nВыделенные признаки из текста:\n{features}\n"
        if features else ""
    )

    return f"""Ты — библиограф. Пользователь отклонил предложенное тобой имя архива.

Архив: «{os.path.basename(archive_path)}»
Отклонённый вариант: «{rejected_name}»
Причина: пользователь счёл, что это имя не отражает содержимое книги.

Источник нового текста: «{target_file}»
{features_block}
Дополнительный текст из документа:
<<<
{preview}
>>>

Задача: предложи ДРУГОЕ, более точное название. Не повторяй отклонённый вариант.
Обрати особое внимание на: имена авторов, заголовки глав, колонтитулы, \
сведения об издании на титульном листе.

Верни JSON:
{{
  "decision": "rename",
  "variants": [
    {{"name": "Автор - Название{archive_ext}", "confidence": 80, "reason": "обоснование"}}
  ]
}}

Требования:
— Кириллица для русских книг, латиница для английских. Без транслита.
— Расширение строго {archive_ext}.
— Формат: «Автор - Название{archive_ext}» или «Название{archive_ext}»."""


# ---------------------------------------------------------------------------
# Промпт 4: тематическая категоризация
# ---------------------------------------------------------------------------

def build_categorize_prompt(book_name: str, extracted_text: str,
                             categories: list) -> str:
    """
    Промпт для определения тематической категории книги.
    Возвращает JSON: {"category": "Название категории"}
    """
    cats    = "\n".join(f"— {c}" for c in categories)
    preview = extracted_text[:1500] if extracted_text else ""

    text_block = (
        f"\nФрагмент содержимого книги:\n<<<\n{preview}\n>>>"
        if preview else ""
    )

    return f"""Ты — библиотечный классификатор. Определи тематическую рубрику книги.

Название книги: «{book_name}»
{text_block}

Доступные рубрики:
{cats}

Выбери ОДНУ наиболее подходящую рубрику из списка.
Руководствуйся содержанием, а не формальными признаками названия.
Если книга охватывает несколько тем — выбирай основную.
Если тема не соответствует ни одной рубрике — выбирай последнюю («Разное»).

Верни только JSON:
{{"category": "Точное название рубрики из списка выше"}}"""
