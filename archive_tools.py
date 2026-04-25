import os
import patoolib
import fnmatch
from typing import Dict, Any, List


def extract_archive(archive_path: str, output_dir: str) -> None:
    """Распаковывает архив в указанную директорию"""
    try:
        patoolib.extract_archive(archive_path, outdir=output_dir)
    except Exception as e:
        raise Exception(f"Ошибка распаковки архива: {e}")


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
                for encoding in ('utf-8', 'cp1251', 'cp866'):
                    try:
                        with open(full_path, 'r', encoding=encoding, errors='strict') as f:
                            content['metadata_content'][name] = f.read(2000)
                        break
                    except (UnicodeDecodeError, UnicodeError):
                        continue
                    except Exception:
                        break

    return content


def find_file_by_pattern(files_list: list, pattern: str) -> str:
    """Возвращает первый файл, подходящий под wildcard-шаблон"""
    for file_info in files_list:
        if file_info['type'] == 'file' and fnmatch.fnmatch(file_info['name'], pattern):
            return file_info['name']
    return None
