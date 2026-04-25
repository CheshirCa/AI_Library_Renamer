import os
import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)

# Расширения, которые считаются основным документом книги
DOCUMENT_EXTENSIONS = {'.pdf', '.docx', '.txt', '.fb2', '.djvu', '.epub', '.mobi', '.azw', '.azw3'}
# .zip убран намеренно: вложенный архив не может быть основным документом


def identify_main_document(files_list: list) -> str:
    """
    Определяет основной документ в списке файлов.
    Возвращает поле 'name' самого крупного файла подходящего формата.
    """
    candidates = [
        f for f in files_list
        if f.get('type') == 'file'
        and os.path.splitext(f['name'])[1].lower() in DOCUMENT_EXTENSIONS
    ]

    if not candidates:
        return ""

    candidates.sort(key=lambda x: x.get('size') or 0, reverse=True)
    return candidates[0]['name']


def extract_text_data(file_path: str, parameters: Dict[str, Any]) -> str:
    """
    Извлекает текст из файла через модульную систему обработчиков formats/.
    """
    from formats import extract_text_data as fmt_extract
    return fmt_extract(file_path, parameters)
