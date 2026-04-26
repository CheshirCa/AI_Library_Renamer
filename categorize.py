"""
categorize.py — тематическая сортировка архивов с книгами по папкам.

Использует уже существующую инфраструктуру проекта:
  config.py      — BOOK_CATEGORIES, OUTPUT_BASE_DIR
  llm_client.py  — send_to_llm
  archive_tools  — распаковка
  formats/       — обработчики форматов (PDF, DjVu, FB2 и т.д.)
  file_tools.py  — identify_main_document, extract_text_data

Алгоритм для каждого архива:
  1. Смотрим на имя файла — если название информативное, LLM сразу даёт категорию
  2. Если имя неинформативное или LLM не уверен — распаковываем и читаем текст
  3. Если и после анализа текста неясно — перемещаем в "Разное"

Использование:
  python categorize.py --file "Шебес - Теория цепей.rar"
  python categorize.py --dir "D:\\Книги" --output-dir "D:\\Книги_sorted"
  python categorize.py --dir "D:\\Книги" --auto   # без вопросов
"""

import os
import re
import sys
import json
import shutil
import logging
import tempfile
import argparse
from typing import Optional

# Добавляем папку скрипта в sys.path — чтобы работало при запуске из любой директории
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def _fix_windows_cmdline():
    """
    Исправляет проблему Windows cmd.exe: путь вида "C:\\dir\\" при парсинге
    CommandLineToArgvW сливается со следующим аргументом, потому что \\"
    трактуется как экранированная кавычка.
    Решение: берём сырую командную строку через WinAPI, убираем экранирование
    и перепарсиваем заново.
    """
    if sys.platform != 'win32':
        sys.argv = [a.rstrip('/\\') for a in sys.argv]
        return
    try:
        import ctypes
        import ctypes.wintypes
        kernel32 = ctypes.windll.kernel32
        shell32  = ctypes.windll.shell32
        GetCommandLineW = kernel32.GetCommandLineW
        GetCommandLineW.restype = ctypes.c_wchar_p
        raw   = GetCommandLineW()
        fixed = raw.replace('\\"', '"')
        argc  = ctypes.c_int(0)
        CommandLineToArgvW = shell32.CommandLineToArgvW
        CommandLineToArgvW.restype = ctypes.POINTER(ctypes.c_wchar_p)
        argv_ptr = CommandLineToArgvW(fixed, ctypes.byref(argc))
        if argv_ptr and argc.value > 0:
            all_args = [argv_ptr[i].rstrip('/\\') for i in range(argc.value)]
            ctypes.windll.kernel32.LocalFree(argv_ptr)
            # CommandLineToArgvW включает python.exe как argv[0], а скрипт как argv[1].
            # Python уже убрал интерпретатор из sys.argv, поэтому ищем скрипт
            # по совпадению basename и берём всё после него.
            script = os.path.basename(sys.argv[0]).lower()
            start  = next(
                (i for i, a in enumerate(all_args)
                 if os.path.basename(a).lower() == script),
                None
            )
            if start is not None:
                sys.argv = [sys.argv[0]] + all_args[start + 1:]
            else:
                # Fallback: пропускаем python.exe и скрипт (первые два элемента)
                sys.argv = [sys.argv[0]] + all_args[min(2, len(all_args)):]
        else:
            sys.argv = [a.rstrip('/\\') for a in sys.argv]
    except Exception as e:
        sys.argv = [a.rstrip('/\\') for a in sys.argv]



# Подгружаем конфиг
from config import BOOK_CATEGORIES, OUTPUT_BASE_DIR, OLLAMA_MODEL
from llm_client import send_to_llm
from archive_tools import extract_archive, scan_archive_content
from file_tools import identify_main_document, extract_text_data

logger = logging.getLogger(__name__)

FALLBACK_CATEGORY = "Разное"   # используется если всё провалилось


# -----------------------------------------------------------------------
# Промпты
# -----------------------------------------------------------------------

def _prompt_from_name(filename: str, categories: list) -> str:
    cats = "\n".join(f"- {c}" for c in categories)
    return f"""Определи тематическую категорию книги только по названию файла.

Имя файла: "{filename}"

Доступные категории:
{cats}

Если название файла явно указывает на тему — выбери подходящую категорию.
Если имя файла — случайный набор цифр/букв и не несёт информации — верни need_more_data.
Если тема неясна — тоже верни need_more_data.

Верни JSON одного из двух видов:
{{"decision": "categorize", "category": "Точное название из списка выше"}}
{{"decision": "need_more_data"}}"""


def _prompt_from_text(filename: str, text: str, categories: list) -> str:
    cats = "\n".join(f"- {c}" for c in categories)
    preview = text[:2000]
    return f"""Определи тематическую категорию книги по её содержимому.

Имя файла: "{filename}"
Извлечённый текст:
{preview}

Доступные категории:
{cats}

Выбери ОДНУ наиболее подходящую категорию из списка.
Если тема совершенно неопределима — выбери "{FALLBACK_CATEGORY}".

Верни только JSON:
{{"decision": "categorize", "category": "Точное название из списка выше"}}"""


# -----------------------------------------------------------------------
# Вспомогательные функции
# -----------------------------------------------------------------------

def _parse_llm_response(response: str) -> dict:
    cleaned = re.sub(r'```.*?```', '', response, flags=re.DOTALL).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        logger.warning(f"Не удалось разобрать JSON: {cleaned[:200]}")
        return {}


def _validate_category(category: str, categories: list) -> str:
    """Возвращает категорию из списка или ближайшую к ней."""
    if category in categories:
        return category
    match = next(
        (c for c in categories
         if c.lower() in category.lower() or category.lower() in c.lower()),
        None
    )
    if match:
        logger.info(f"Категория '{category}' → исправлено на '{match}'")
        return match
    logger.warning(f"Категория '{category}' не в списке, используем '{FALLBACK_CATEGORY}'")
    return FALLBACK_CATEGORY


def _move_file(src: str, category: str, base_dir: str) -> bool:
    """Перемещает файл в папку категории. Создаёт папку если нужно."""
    target_dir  = os.path.join(base_dir, category)
    target_path = os.path.join(target_dir, os.path.basename(src))

    # Если файл с таким именем уже есть — добавляем суффикс
    if os.path.exists(target_path):
        stem, ext   = os.path.splitext(os.path.basename(src))
        target_path = os.path.join(target_dir, f"{stem}_dup{ext}")

    try:
        os.makedirs(target_dir, exist_ok=True)
        shutil.move(src, target_path)
        logger.info(f"Перемещён [{category}]: {os.path.basename(src)}")
        print(f"  → [{category}] {target_path}")
        return True
    except Exception as e:
        logger.error(f"Ошибка перемещения: {e}")
        return False


# -----------------------------------------------------------------------
# Основная логика
# -----------------------------------------------------------------------

def categorize_archive(archive_path: str, base_dir: str,
                       categories: list, auto: bool = False) -> None:
    """Определяет категорию одного архива и перемещает его."""
    filename = os.path.basename(archive_path)
    print(f"\n  {filename}")

    # --- Шаг 1: пробуем определить по имени файла ---
    prompt   = _prompt_from_name(filename, categories)
    response = _parse_llm_response(send_to_llm(prompt))

    if response.get('decision') == 'categorize':
        category = _validate_category(response.get('category', ''), categories)
        logger.debug(f"Категория по имени: {category}")
    else:
        # --- Шаг 2: распаковываем и читаем текст ---
        category = _categorize_from_content(archive_path, filename, categories)

    # --- Подтверждение или авто ---
    print(f"  Категория: {category}")

    if not auto:
        answer = input(
            f"  Переместить в '{category}'? [y/Enter] [n — пропустить] [другое — своя категория]: "
        ).strip()
        if answer.lower() in ('n', 'н', 'no', 'нет'):
            print("  Пропускаем.")
            return
        if answer and answer.lower() not in ('y', 'д', 'yes', 'да', ''):
            category = answer  # пользователь ввёл свою категорию

    _move_file(archive_path, category, base_dir)


def _categorize_from_content(archive_path: str, filename: str,
                              categories: list) -> str:
    """Распаковывает архив, извлекает текст и определяет категорию."""
    tmp_dir = tempfile.mkdtemp()
    try:
        try:
            extract_archive(archive_path, tmp_dir)
        except Exception as e:
            logger.error(f"Не удалось распаковать: {e}")
            return FALLBACK_CATEGORY

        content  = scan_archive_content(tmp_dir)
        files    = content.get('files', [])
        main_doc = identify_main_document(files)

        if not main_doc:
            logger.warning("Основной документ не найден")
            return FALLBACK_CATEGORY

        file_obj = next((f for f in files if f['name'] == main_doc), None)
        if not file_obj:
            return FALLBACK_CATEGORY

        # Извлекаем текст
        params = {'type': 'first_chars', 'amount': 2000}
        text   = extract_text_data(file_obj['path'], params)

        if not text or len(text.strip()) < 20:
            logger.warning(f"Не удалось извлечь текст из '{main_doc}'")
            return FALLBACK_CATEGORY

        logger.debug(f"Извлечено {len(text)} символов из '{main_doc}'")

        # Спрашиваем LLM с текстом
        prompt   = _prompt_from_text(filename, text, categories)
        response = _parse_llm_response(send_to_llm(prompt))

        if response.get('decision') == 'categorize':
            return _validate_category(response.get('category', ''), categories)

        return FALLBACK_CATEGORY

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# -----------------------------------------------------------------------
# Точка входа
# -----------------------------------------------------------------------

def process_directory(dir_path: str, base_dir: str, categories: list,
                      auto: bool, extensions: tuple) -> None:
    archives = [
        os.path.join(dir_path, f)
        for f in sorted(os.listdir(dir_path))
        if os.path.isfile(os.path.join(dir_path, f))
        and os.path.splitext(f)[1].lower() in extensions
    ]
    if not archives:
        print(f"Архивов не найдено в: {dir_path}")
        return
    print(f"Найдено архивов: {len(archives)}")
    for i, path in enumerate(archives, 1):
        print(f"\n[{i}/{len(archives)}]", end="")
        try:
            categorize_archive(path, base_dir, categories, auto)
        except KeyboardInterrupt:
            print("\nПрервано пользователем.")
            sys.exit(0)
        except Exception as e:
            logger.error(f"Ошибка при обработке {os.path.basename(path)}: {e}")


def main():
    parser = argparse.ArgumentParser(
        description="Тематическая сортировка архивов с книгами по папкам"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--file", help="Путь к одному архиву")
    group.add_argument("--dir",  help="Папка с архивами")

    parser.add_argument(
        "--output-dir", default=None,
        help=f"Куда раскладывать (по умолчанию — OUTPUT_BASE_DIR из config.py: {OUTPUT_BASE_DIR})"
    )
    parser.add_argument(
        "--auto", action="store_true",
        help="Не спрашивать подтверждения, перемещать сразу"
    )
    parser.add_argument("--debug", action="store_true", help="Подробный вывод")
    _fix_windows_cmdline()
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )

    base_dir = args.output_dir or OUTPUT_BASE_DIR
    if not base_dir:
        print(
            "Ошибка: не указана папка назначения.\n"
            "Используйте --output-dir или пропишите OUTPUT_BASE_DIR в config.py"
        )
        sys.exit(1)

    categories = BOOK_CATEGORIES
    if FALLBACK_CATEGORY not in categories:
        categories = list(categories) + [FALLBACK_CATEGORY]

    extensions = ('.zip', '.rar', '.fb2', '.epub', '.mobi', '.pdf', '.djvu')

    print(f"Модель:     {OLLAMA_MODEL}")
    print(f"Назначение: {base_dir}")
    print(f"Категории:  {', '.join(categories)}")

    if args.file:
        if not os.path.isfile(args.file):
            logger.error(f"Файл не найден: {args.file}")
            sys.exit(1)
        categorize_archive(args.file, base_dir, categories, args.auto)

    elif args.dir:
        if not os.path.isdir(args.dir):
            logger.error(f"Папка не найдена: {args.dir}")
            sys.exit(1)
        process_directory(args.dir, base_dir, categories, args.auto, extensions)


if __name__ == "__main__":
    main()
