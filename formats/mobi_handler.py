import os
import struct
import logging
import tempfile
import shutil
from typing import Dict, Any
from .base_handler import BaseFormatHandler

logger = logging.getLogger(__name__)

# EXTH record types нужные нам
_EXTH_TITLE  = 503   # обновлённое название
_EXTH_AUTHOR = 100
_EXTH_SUBJECT = 105
_EXTH_DESCRIPTION = 103


class MOBIHandler(BaseFormatHandler):
    """
    Обработчик для MOBI/AZW файлов.

    Стратегия:
      1. Читаем EXTH-метаданные из заголовка (title, author) — мгновенно, без зависимостей
      2. Если метаданных нет — извлекаем текст через пакет `mobi`
      3. Fallback: имя из PalmDB-заголовка (первые 32 байта файла)
    """

    @staticmethod
    def can_handle(file_path: str) -> bool:
        return BaseFormatHandler.get_file_extension(file_path) in ('.mobi', '.azw', '.azw3')

    @staticmethod
    def extract_text(file_path: str, parameters: Dict[str, Any]) -> str:
        amount = parameters.get('amount', 2000)

        # --- Попытка 1: EXTH метаданные (title + author) ---
        meta = MOBIHandler._read_exth_metadata(file_path)
        if meta.get('title'):
            parts = []
            if meta.get('author'):
                parts.append(f"Автор: {meta['author']}")
            parts.append(f"Название: {meta['title']}")
            if meta.get('subject'):
                parts.append(f"Тема: {meta['subject']}")
            if meta.get('description'):
                parts.append(f"Описание: {meta['description'][:200]}")
            result = "\n".join(parts)
            logger.info(f"MOBI EXTH: {result[:100]}")
            return result

        # --- Попытка 2: текст через пакет mobi ---
        text = MOBIHandler._extract_via_mobi_package(file_path, amount)
        if text:
            return text

        # --- Fallback: имя из PalmDB заголовка ---
        palm_name = MOBIHandler._read_palm_name(file_path)
        if palm_name:
            logger.info(f"MOBI PalmDB name: {palm_name}")
            return f"Название (из заголовка): {palm_name}"

        return ""

    @staticmethod
    def get_metadata(file_path: str) -> Dict[str, str]:
        return MOBIHandler._read_exth_metadata(file_path)

    # ------------------------------------------------------------------
    # Внутренние методы
    # ------------------------------------------------------------------

    @staticmethod
    def _read_palm_name(file_path: str) -> str:
        """Читает имя базы данных из PalmDB-заголовка (первые 32 байта)."""
        try:
            with open(file_path, 'rb') as f:
                raw = f.read(32)
            name = raw.rstrip(b'\x00').decode('latin-1', errors='replace').strip()
            return name if len(name) > 2 else ""
        except Exception:
            return ""

    @staticmethod
    def _read_exth_metadata(file_path: str) -> Dict[str, str]:
        """
        Парсит EXTH-блок MOBI файла и возвращает метаданные.
        Формат: PalmDB (78 байт) → PalmRecord[0] → MOBI header → EXTH header → records
        """
        try:
            with open(file_path, 'rb') as f:
                data = f.read(min(64 * 1024, os.path.getsize(file_path)))  # первые 64KB

            # PalmDB: смещение на записи (offset list начинается с байта 78)
            if len(data) < 78:
                return {}

            num_records = struct.unpack_from('>H', data, 76)[0]
            if num_records < 1 or 78 + num_records * 8 > len(data):
                return {}

            # Смещение первой записи (Record 0)
            rec0_offset = struct.unpack_from('>I', data, 78)[0]
            if rec0_offset + 16 > len(data):
                return {}

            rec0 = data[rec0_offset:]

            # Проверяем сигнатуру MOBI
            if len(rec0) < 16 or rec0[16:20] != b'MOBI':
                return {}

            mobi_header_len = struct.unpack_from('>I', rec0, 20)[0]
            if mobi_header_len < 100:
                return {}

            # EXTH начинается сразу после MOBI-заголовка + 16 байт PalmDOC
            exth_offset = 16 + mobi_header_len
            if exth_offset + 12 > len(rec0):
                return {}

            if rec0[exth_offset:exth_offset + 4] != b'EXTH':
                return {}

            exth_len     = struct.unpack_from('>I', rec0, exth_offset + 4)[0]
            num_exth_recs = struct.unpack_from('>I', rec0, exth_offset + 8)[0]

            pos  = exth_offset + 12
            meta = {}

            for _ in range(num_exth_recs):
                if pos + 8 > len(rec0):
                    break
                rec_type = struct.unpack_from('>I', rec0, pos)[0]
                rec_len  = struct.unpack_from('>I', rec0, pos + 4)[0]
                if rec_len < 8 or pos + rec_len > len(rec0):
                    break
                value_bytes = rec0[pos + 8: pos + rec_len]
                pos += rec_len

                # Декодируем строковые поля
                for enc in ('utf-8', 'cp1251', 'latin-1'):
                    try:
                        value = value_bytes.decode(enc).strip('\x00').strip()
                        break
                    except UnicodeDecodeError:
                        continue
                else:
                    continue

                if rec_type == _EXTH_TITLE and value:
                    meta['title'] = value
                elif rec_type == _EXTH_AUTHOR and value:
                    meta['author'] = value
                elif rec_type == _EXTH_SUBJECT and value:
                    meta['subject'] = value
                elif rec_type == _EXTH_DESCRIPTION and value:
                    meta['description'] = value

            return meta

        except Exception as e:
            logger.debug(f"EXTH parse error: {e}")
            return {}

    @staticmethod
    def _extract_via_mobi_package(file_path: str, max_chars: int) -> str:
        """Извлекает текст через пакет `mobi` (pip install mobi)."""
        try:
            import mobi as mobi_lib
        except ImportError:
            logger.debug("Пакет mobi не установлен (pip install mobi)")
            return ""

        tmp_dir = tempfile.mkdtemp()
        try:
            # mobi.extract возвращает (tempdir, filepath_to_main_file)
            _, extracted_path = mobi_lib.extract(file_path)
            ext = os.path.splitext(extracted_path)[1].lower()

            if ext in ('.html', '.htm', '.xhtml'):
                with open(extracted_path, 'r', encoding='utf-8', errors='ignore') as f:
                    raw = f.read(max_chars * 3)
                # Убираем теги
                import re
                text = re.sub(r'<[^>]+>', ' ', raw)
                text = re.sub(r'\s+', ' ', text).strip()
                return text[:max_chars]

            elif ext in ('.txt',):
                with open(extracted_path, 'r', encoding='utf-8', errors='ignore') as f:
                    return f.read(max_chars)

            return ""

        except Exception as e:
            logger.warning(f"mobi.extract ошибка: {e}")
            return ""
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)
