import os
import re
import sys
import json
import logging
import tempfile
import shutil
import argparse
import fnmatch

# Позволяет запускать скрипт из любой директории
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


from file_tools import identify_main_document, extract_text_data
from llm_client import send_to_llm
from prompts import build_initial_prompt, build_text_analysis_prompt, build_retry_prompt, build_categorize_prompt
from archive_tools import extract_archive, scan_archive_content

logger = logging.getLogger(__name__)

MAX_LLM_DEPTH   = 5   # максимум автоматических итераций need_more_data
MAX_USER_ROUNDS = 3   # максимум раундов пользовательского отказа


def sanitize_filename(name: str) -> str:
    name = os.path.basename(name)
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name)
    name = name.strip('. ')
    return name or "renamed_archive"


# Расширения файлов-содержимого, которые LLM может ошибочно добавить в имя архива
_CONTENT_EXTENSIONS = {'.djvu', '.pdf', '.fb2', '.epub', '.docx', '.doc',
                       '.txt', '.png', '.jpg', '.jpeg', '.zip', '.rar', '.7z'}

def _fix_extension(proposed_name: str, archive_path: str) -> str:
    """
    Гарантирует что имя архива имеет правильное расширение.
    Убирает расширения файлов-содержимого которые LLM могла вставить в имя.
    Пример: 'Книга.djvu.rar' → 'Книга.rar'
             'Книга.djvu'     → 'Книга.rar'
             'Книга.rar'      → 'Книга.rar'
    """
    correct_ext = os.path.splitext(archive_path)[1].lower()

    # Сначала снимаем правильное расширение если оно уже есть последним
    stem = proposed_name
    if stem.lower().endswith(correct_ext):
        stem = stem[:-len(correct_ext)]

    # Затем снимаем все лишние расширения содержимого
    for _ in range(5):
        base, ext = os.path.splitext(stem)
        if ext.lower() in _CONTENT_EXTENSIONS:
            stem = base
        else:
            break

    return stem + correct_ext


def rename_file(old_path: str, new_name: str) -> bool:
    if not new_name:
        logger.error("Получено пустое имя файла")
        return False
    new_name = sanitize_filename(new_name)
    dir_path = os.path.dirname(old_path)
    new_path = os.path.join(dir_path, new_name)
    if os.path.exists(new_path) and new_path != old_path:
        logger.error(f"Файл уже существует: {new_path}")
        return False
    try:
        os.rename(old_path, new_path)
        logger.info(f"Переименован: {os.path.basename(old_path)} -> {new_name}")
        print(f"OK Переименован: {new_path}")
        return True
    except Exception as e:
        logger.error(f"Ошибка при переименовании: {e}")
        return False


def _resolve_file(archive_content: dict, target: str):
    files = archive_content['files']
    file_obj = next((f for f in files if f['name'] == target), None)
    if file_obj:
        return file_obj
    file_obj = next((f for f in files if fnmatch.fnmatch(f['name'], target)), None)
    if file_obj:
        return file_obj
    main_doc_name = identify_main_document(files)
    file_obj = next((f for f in files if f['name'] == main_doc_name), None)
    if file_obj:
        logger.info(f"Fallback: используем '{file_obj['name']}'")
    return file_obj


ERROR_PREFIXES = ("Ошибка", "Error", "OCR недоступен", "Не удалось", "WinError")

def _is_extraction_error(text: str) -> bool:
    """True если текст является сообщением об ошибке, а не содержимым файла."""
    if not text or len(text.strip()) < 20:
        return True
    return any(text.strip().startswith(p) for p in ERROR_PREFIXES)


def _extract_text_for_file(archive_content: dict, target: str, parameters: dict):
    file_obj = _resolve_file(archive_content, target)
    if not file_obj:
        logger.error("В архиве не найдено подходящих файлов")
        return None, None
    text = extract_text_data(file_obj["path"], parameters)
    if _is_extraction_error(text):
        fname = file_obj['name']
        logger.warning(f"Не удалось извлечь текст из '{fname}': {(text or '')[:120]}")
        return file_obj, None   # None = ошибка извлечения
    logger.debug(f"Извлечено {len(text)} символов из '{file_obj['name']}'")
    return file_obj, text


def _ask_user_about_name(proposed_name: str) -> str:
    """
    Спрашивает пользователя об имени. Возвращает:
      'accept'  - принять и переименовать
      'retry'   - не то, искать дальше
      'skip'    - пропустить архив
      <строка>  - своё имя, введённое пользователем
    """
    clean = sanitize_filename(proposed_name)
    print(f"\n  Предлагаемое имя: {clean}")
    print("  [y] Принять   [n] Не то, искать дальше   [s] Пропустить   [имя] Ввести своё")
    answer = input("  > ").strip()

    if answer.lower() in ('y', 'д', 'yes', 'да'):
        return 'accept'
    if answer.lower() in ('s', 'п', 'skip', 'пропустить', ''):
        return 'skip'
    if answer.lower() in ('n', 'н', 'no', 'нет'):
        return 'retry'
    return answer  # пользователь ввёл своё имя


def _ask_manual_name(archive_path: str, extracted_texts: list = None) -> None:
    """
    Последний шанс: показывает извлечённый текст и предлагает ввести имя вручную.
    """
    print(f"\n  Не удалось автоматически определить название: {os.path.basename(archive_path)}")

    # Показываем фрагмент текста чтобы пользователь мог ориентироваться
    if extracted_texts:
        last = extracted_texts[-1]
        preview = (last.get('text') or '').strip()[:400].replace('\n', ' ')
        if preview:
            print(f"\n  Фрагмент текста из '{last.get('file', '?')}':")
            print(f"  {preview}")
            print()

    answer = input("  Введите имя вручную (Enter — пропустить): ").strip()
    if answer:
        archive_ext = os.path.splitext(archive_path)[1]
        if not answer.endswith(archive_ext):
            answer += archive_ext
        rename_file(archive_path, answer)
    else:
        print("  Пропускаем.")


def handle_llm_decision(archive_path: str, archive_content: dict,
                        llm_response, auto_rename: bool = False,
                        _depth: int = 0, _user_round: int = 0,
                        _extracted_texts: list = None):
    if _extracted_texts is None:
        _extracted_texts = []

    if _depth >= MAX_LLM_DEPTH:
        logger.info(f"Лимит автоматических итераций LLM ({MAX_LLM_DEPTH}) исчерпан.")
        _ask_manual_name(archive_path, _extracted_texts)
        return

    if _user_round >= MAX_USER_ROUNDS:
        print(f"\n  Достигнут лимит попыток ({MAX_USER_ROUNDS}).")
        _ask_manual_name(archive_path, _extracted_texts)
        return

    # Разбираем JSON
    if isinstance(llm_response, str):
        cleaned = re.sub(r'```.*?```', '', llm_response, flags=re.DOTALL).strip()
        try:
            llm_response = json.loads(cleaned)
        except json.JSONDecodeError as e:
            logger.error(f"Ошибка разбора JSON: {e}\n{cleaned[:300]}")
            _ask_manual_name(archive_path, _extracted_texts)
            return
    elif not isinstance(llm_response, dict):
        logger.error(f"Неожиданный тип ответа: {type(llm_response)}")
        return

    decision = llm_response.get('decision')

    if decision == 'rename':
        new_name = llm_response.get('new_name')
        if not new_name:
            logger.error("LLM вернула 'rename' без 'new_name'")
            _ask_manual_name(archive_path, _extracted_texts)
            return

        new_name = _fix_extension(new_name, archive_path)

        # Вспомогательная функция: переименовать и сразу категоризировать
        def _do_rename_and_categorize(path, name):
            if rename_file(path, name):
                new_path = os.path.join(os.path.dirname(path), sanitize_filename(name))
                extracted_text = _extracted_texts[-1]['text'] if _extracted_texts else ""
                categorize_and_move(new_path, name, extracted_text, auto_rename)

        if auto_rename:
            _do_rename_and_categorize(archive_path, new_name)
            return

        user_answer = _ask_user_about_name(new_name)

        if user_answer == 'accept':
            _do_rename_and_categorize(archive_path, new_name)

        elif user_answer == 'skip':
            print("  Пропускаем.")

        elif user_answer == 'retry':
            print("  Ищем дополнительную информацию...")
            _retry_with_more_data(
                archive_path, archive_content, new_name,
                auto_rename, _depth, _user_round + 1, _extracted_texts
            )

        else:
            # Пользователь ввёл своё имя
            archive_ext = os.path.splitext(archive_path)[1]
            custom = user_answer if user_answer.endswith(archive_ext) else user_answer + archive_ext
            _do_rename_and_categorize(archive_path, custom)

    elif decision == 'need_more_data':
        target = llm_response.get('target', '')
        params = llm_response.get('parameters', {'type': 'first_chars', 'amount': 2000})

        file_obj, text = _extract_text_for_file(archive_content, target, params)
        if not file_obj:
            _ask_manual_name(archive_path, _extracted_texts)
            return
        if text is None:
            logger.warning("Не удалось извлечь текст, пробуем OCR через ручной ввод")
            _ask_manual_name(archive_path, _extracted_texts)
            return

        _extracted_texts.append({'file': file_obj['name'], 'text': text,
                                  'amount': params.get('amount', 2000)})

        prompt = build_text_analysis_prompt(
            archive_path, archive_content, file_obj['name'], text
        )
        response = send_to_llm(prompt)
        handle_llm_decision(
            archive_path, archive_content, response,
            auto_rename, _depth + 1, _user_round, _extracted_texts
        )

    else:
        logger.warning(f"Неизвестное решение LLM: {decision!r}")
        _ask_manual_name(archive_path, _extracted_texts)


def _retry_with_more_data(archive_path: str, archive_content: dict,
                          rejected_name: str, auto_rename: bool,
                          _depth: int, _user_round: int,
                          _extracted_texts: list):
    """
    Пользователь отверг имя. Извлекаем больше текста (или берём следующий файл)
    и отправляем в LLM с контекстом об отклонённом варианте.
    """
    files = archive_content['files']
    already = {e['file'] for e in _extracted_texts}
    doc_exts = {'.pdf', '.fb2', '.epub', '.djvu', '.docx', '.txt'}

    # Ищем необработанный файл подходящего формата
    candidates = [
        f for f in files
        if f.get('type') == 'file'
        and f['name'] not in already
        and os.path.splitext(f['name'])[1].lower() in doc_exts
    ]

    if candidates:
        file_obj = candidates[0]
        params   = {'type': 'first_chars', 'amount': 3000}
        logger.info(f"Пробуем следующий файл: '{file_obj['name']}'")
    else:
        # Все файлы уже обработаны — берём больше текста из основного
        main_name = identify_main_document(files)
        file_obj  = next((f for f in files if f['name'] == main_name), None)
        if not file_obj:
            print("  Дополнительных данных нет.")
            _ask_manual_name(archive_path, _extracted_texts)
            return
        prev_amount = max((e.get('amount', 2000) for e in _extracted_texts), default=2000)
        new_amount  = min(prev_amount * 2, 8000)
        params      = {'type': 'first_chars', 'amount': new_amount}
        logger.info(f"Расширяем выборку из '{file_obj['name']}' до {new_amount} символов")

    text = extract_text_data(file_obj['path'], params)
    if _is_extraction_error(text):
        logger.warning(f"Не удалось извлечь текст из '{file_obj['name']}'")
        _ask_manual_name(archive_path, _extracted_texts)
        return
    _extracted_texts.append({'file': file_obj['name'], 'text': text,
                              'amount': params.get('amount', 2000)})

    prompt = build_retry_prompt(
        archive_path, archive_content,
        rejected_name, file_obj['name'], text
    )
    response = send_to_llm(prompt)
    handle_llm_decision(
        archive_path, archive_content, response,
        auto_rename, _depth, _user_round, _extracted_texts
    )



def categorize_and_move(file_path: str, book_name: str,
                        extracted_text: str, auto_rename: bool = False) -> None:
    """
    Определяет тематическую категорию книги и перемещает файл в соответствующую папку.
    Использует OUTPUT_BASE_DIR и BOOK_CATEGORIES из config.py.
    """
    from config import OUTPUT_BASE_DIR, BOOK_CATEGORIES

    if not OUTPUT_BASE_DIR:
        return  # сортировка отключена

    if not os.path.isfile(file_path):
        logger.warning(f"categorize_and_move: файл не найден: {file_path}")
        return

    # Запрашиваем категорию у LLM
    prompt   = build_categorize_prompt(book_name, extracted_text, BOOK_CATEGORIES)
    response = send_to_llm(prompt)

    try:
        cleaned  = re.sub(r'```.*?```', '', response, flags=re.DOTALL).strip()
        category = json.loads(cleaned).get('category', '').strip()
    except (json.JSONDecodeError, AttributeError):
        logger.warning(f"Не удалось разобрать категорию: {response[:100]}")
        category = BOOK_CATEGORIES[-1]  # fallback — последняя категория

    # Проверяем что категория из списка (LLM иногда придумывает свои)
    if category not in BOOK_CATEGORIES:
        # Ищем наиболее близкую
        match = next((c for c in BOOK_CATEGORIES if c.lower() in category.lower()
                      or category.lower() in c.lower()), BOOK_CATEGORIES[-1])
        logger.info(f"Категория '{category}' не в списке, используем '{match}'")
        category = match

    target_dir  = os.path.join(OUTPUT_BASE_DIR, category)
    target_path = os.path.join(target_dir, os.path.basename(file_path))

    print(f"  Категория: {category}")

    if not auto_rename:
        answer = input(f"  Переместить в '{category}'? [y/N/другая категория]: ").strip()
        if answer.lower() in ('n', 'н', ''):
            print("  Оставляем на месте.")
            return
        if answer.lower() not in ('y', 'д', 'yes', 'да'):
            # Пользователь ввёл название категории вручную
            category    = answer
            target_dir  = os.path.join(OUTPUT_BASE_DIR, category)
            target_path = os.path.join(target_dir, os.path.basename(file_path))

    os.makedirs(target_dir, exist_ok=True)

    # Если файл с таким именем уже есть — добавляем суффикс
    if os.path.exists(target_path):
        stem, ext   = os.path.splitext(os.path.basename(file_path))
        target_path = os.path.join(target_dir, f"{stem}_dup{ext}")

    try:
        shutil.move(file_path, target_path)
        logger.info(f"Перемещён в [{category}]: {target_path}")
        print(f"  Перемещён: {target_path}")
    except Exception as e:
        logger.error(f"Ошибка при перемещении: {e}")


def analyze_archive(archive_path: str, auto_rename: bool = False) -> None:
    logger.info(f"Анализируем: {archive_path}")
    tmp_dir = tempfile.mkdtemp()
    try:
        extract_archive(archive_path, tmp_dir)
        archive_content = scan_archive_content(tmp_dir)

        if not archive_content['files']:
            logger.error("Архив пуст")
            return

        logger.debug(f"Файлов: {len(archive_content['files'])}")
        if archive_content['metadata_content']:
            logger.info(f"Метафайлы: {list(archive_content['metadata_content'].keys())}")

        prompt       = build_initial_prompt(os.path.basename(archive_path), archive_content)
        response_str = send_to_llm(prompt)

        try:
            response = json.loads(response_str)
        except json.JSONDecodeError as e:
            logger.error(f"Ошибка разбора JSON: {e}")
            return

        handle_llm_decision(archive_path, archive_content, response, auto_rename)

    except Exception as e:
        logger.error(f"Ошибка при обработке: {e}")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def process_directory(dir_path: str, auto_rename: bool = False,
                      extensions: tuple = ('.zip', '.rar')) -> None:
    archives = [
        os.path.join(dir_path, f)
        for f in os.listdir(dir_path)
        if os.path.isfile(os.path.join(dir_path, f))
        and os.path.splitext(f)[1].lower() in extensions
    ]
    if not archives:
        logger.warning(f"Архивов не найдено в: {dir_path}")
        return
    logger.info(f"Найдено архивов: {len(archives)}")
    for i, path in enumerate(archives, 1):
        print(f"\n{'='*60}")
        print(f"[{i}/{len(archives)}] {os.path.basename(path)}")
        print('='*60)
        analyze_archive(path, auto_rename)


def main():
    parser = argparse.ArgumentParser(description="Авто-переименование архивов с книгами")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--file", help="Путь к одному архиву")
    group.add_argument("--dir",  help="Папка с архивами")
    parser.add_argument("--rename", action="store_true",
                        help="Автоматически применять имя (без вопросов)")
    parser.add_argument("--output-dir", default=None,
                        help="Папка для тематической сортировки (переопределяет OUTPUT_BASE_DIR из config.py)")
    parser.add_argument("--debug",  action="store_true", help="Подробный вывод")
    _fix_windows_cmdline()
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )

    # Переопределяем OUTPUT_BASE_DIR если передан --output-dir
    if args.output_dir:
        import config
        config.OUTPUT_BASE_DIR = args.output_dir

    if args.file:
        if not os.path.isfile(args.file):
            logger.error(f"Файл не найден: {args.file}")
            return
        analyze_archive(args.file, auto_rename=args.rename)
    elif args.dir:
        if not os.path.isdir(args.dir):
            logger.error(f"Папка не найдена: {args.dir}")
            return
        process_directory(args.dir, auto_rename=args.rename)


if __name__ == "__main__":
    main()
