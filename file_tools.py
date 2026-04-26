import os
import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)

# Расширения, которые считаются основным документом книги
DOCUMENT_EXTENSIONS = {'.pdf', '.docx', '.doc', '.rtf', '.txt', '.fb2', '.djvu', '.epub', '.mobi', '.azw', '.azw3'}
# .zip убран намеренно: вложенный архив не может быть основным документом

# Приоритет форматов: чем ниже индекс — тем предпочтительнее
_FORMAT_PRIORITY = ['.pdf', '.djvu', '.fb2', '.epub', '.mobi', '.azw3', '.azw',
                    '.docx', '.doc', '.rtf', '.txt']


def _name_is_informative(name: str) -> bool:
    """True если имя файла несёт смысловую информацию (не цифры, не короткий код)."""
    stem = os.path.splitext(os.path.basename(name))[0]
    if stem.isdigit():
        return False
    if len(stem) <= 2:
        return False
    return True


def identify_main_document(files_list: list) -> str:
    """
    Определяет основной документ в списке файлов.

    Приоритеты:
      1. Информативное имя + приоритетный формат + большой размер
      2. Любой документ с информативным именем
      3. Самый большой документ (fallback)
    """
    candidates = [
        f for f in files_list
        if f.get('type') == 'file'
        and os.path.splitext(f['name'])[1].lower() in DOCUMENT_EXTENSIONS
    ]

    if not candidates:
        return ""

    def sort_key(f):
        ext = os.path.splitext(f['name'])[1].lower()
        fmt_rank = _FORMAT_PRIORITY.index(ext) if ext in _FORMAT_PRIORITY else 99
        informative = 0 if _name_is_informative(f['name']) else 1
        size = -(f.get('size') or 0)  # больше = лучше
        return (informative, fmt_rank, size)

    candidates.sort(key=sort_key)
    return candidates[0]['name']


def extract_text_data(file_path: str, parameters: Dict[str, Any]) -> str:
    """
    Извлекает текст из файла через модульную систему обработчиков formats/.
    """
    from formats import extract_text_data as fmt_extract
    return fmt_extract(file_path, parameters)
