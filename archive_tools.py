import os
import sys
import shutil
import subprocess
import patoolib
import fnmatch
from typing import Dict, Any, List

import logging
logger = logging.getLogger(__name__)

# Пути к архиваторам на Windows — ищем в типичных местах
_WINRAR_PATHS = [
    r"C:\Program Files\WinRAR\rar.exe",
    r"C:\Program Files\WinRAR\WinRAR.exe",
    r"C:\Program Files (x86)\WinRAR\rar.exe",
    r"C:\Program Files (x86)\WinRAR\WinRAR.exe",
]
_7ZIP_PATHS = [
    r"C:\Program Files\7-Zip\7z.exe",
    r"C:\Program Files (x86)\7-Zip\7z.exe",
]


def _find_tool(paths: list) -> str:
    """Ищет инструмент в PATH или по известным путям."""
    name = os.path.basename(paths[0])
    found = shutil.which(name)
    if found:
        return found
    for p in paths:
        if os.path.isfile(p):
            return p
    return ""


def _extract_with_subprocess(archive_path: str, output_dir: str) -> bool:
    """
    Прямой вызов WinRAR или 7-Zip через subprocess с явным указанием кодировки.
    Возвращает True при успехе.
    """
    ext = os.path.splitext(archive_path)[1].lower()

    # WinRAR — умеет и RAR и ZIP
    rar = _find_tool(_WINRAR_PATHS)
    if rar and ext in ('.rar', '.zip', '.7z'):
        try:
            result = subprocess.run(
                [rar, 'x', '-y', '-o+', archive_path, output_dir + os.sep],
                capture_output=True,
                timeout=120,
            )
            if result.returncode in (0, 1):  # 1 = предупреждение, не ошибка
                return True
            err = result.stderr.decode('cp866', errors='replace').strip()
            logger.warning(f"WinRAR вернул код {result.returncode}: {err[:200]}")
        except Exception as e:
            logger.debug(f"WinRAR subprocess ошибка: {e}")

    # 7-Zip — универсальный fallback
    z = _find_tool(_7ZIP_PATHS)
    if z:
        try:
            result = subprocess.run(
                [z, 'x', '-y', f'-o{output_dir}', archive_path],
                capture_output=True,
                timeout=120,
            )
            if result.returncode == 0:
                return True
            err = result.stderr.decode('cp866', errors='replace').strip()
            logger.warning(f"7-Zip вернул код {result.returncode}: {err[:200]}")
        except Exception as e:
            logger.debug(f"7-Zip subprocess ошибка: {e}")

    return False


def extract_archive(archive_path: str, output_dir: str) -> None:
    """
    Распаковывает архив в указанную директорию.

    Стратегия:
      1. patoolib — универсальный, работает с большинством форматов
      2. При UnicodeDecodeError (WinRAR выдаёт cp866, patoolib ожидает UTF-8) —
         прямой вызов WinRAR или 7-Zip через subprocess
    """
    try:
        patoolib.extract_archive(archive_path, outdir=output_dir)
        return
    except UnicodeDecodeError as e:
        logger.warning(
            f"patoolib: ошибка кодировки при чтении вывода архиватора ({e}). "
            f"Пробуем прямой вызов subprocess..."
        )
    except Exception as e:
        err_str = str(e)
        if 'codec' in err_str or 'decode' in err_str or 'encode' in err_str:
            logger.warning(
                f"patoolib: возможная проблема кодировки: {e}. "
                f"Пробуем прямой вызов subprocess..."
            )
        else:
            raise Exception(f"Ошибка распаковки архива: {e}")

    # Fallback: прямой subprocess
    if _extract_with_subprocess(archive_path, output_dir):
        logger.info("Архив распакован через прямой вызов архиватора")
        return

    raise Exception(
        f"Не удалось распаковать архив '{os.path.basename(archive_path)}'. "
        f"Убедитесь что WinRAR или 7-Zip установлен и доступен."
    )


def scan_archive_content(directory: str) -> Dict[str, Any]:
    """
    Сканирует содержимое распакованного архива.
    Возвращает список файлов (с полем 'path') и содержимое метафайлов.
    """
    content: Dict[str, Any] = {
        'files': [],
        'metadata_content': {}
    }

    META_NAMES = {'file_id.diz', 'readme.txt', 'readme.md', 'description.txt'}
    META_PATTERNS = ('*.nfo', 'read*me*')  # ранее '*.nfo' лежал в списке имён — не работало

    for root, dirs, files in os.walk(directory):
        for name in files:
            full_path = os.path.join(root, name)
            rel_path = os.path.relpath(full_path, directory)

            content['files'].append({
                'name': rel_path,
                'path': full_path,          # полный путь нужен для extract_text_data
                'type': 'file',
                'size': os.path.getsize(full_path),
            })

            # Читаем метафайлы (README, DIZ, NFO)
            name_lower = name.lower()
            is_meta = (
                name_lower in META_NAMES
                or any(fnmatch.fnmatch(name_lower, pat) for pat in META_PATTERNS)
            )
            if is_meta:
                try:
                    from formats.txt_handler import decode_text
                    with open(full_path, 'rb') as f:
                        raw = f.read(2000)
                    content['metadata_content'][name] = decode_text(raw)
                except Exception:
                    pass

    return content


def find_file_by_pattern(files_list: list, pattern: str) -> str:
    """Возвращает первый файл, подходящий под wildcard-шаблон"""
    for file_info in files_list:
        if file_info['type'] == 'file' and fnmatch.fnmatch(file_info['name'], pattern):
            return file_info['name']
    return None
