import os
import re
import logging
from typing import Dict, Any
from .base_handler import BaseFormatHandler

logger = logging.getLogger(__name__)


class RTFHandler(BaseFormatHandler):
    """
    Обработчик для RTF файлов.

    Стратегия:
      1. striprtf (pip install striprtf) — чистый Python, надёжно
      2. Regex fallback — убирает RTF-теги вручную, без зависимостей
    """

    @staticmethod
    def can_handle(file_path: str) -> bool:
        return BaseFormatHandler.get_file_extension(file_path) == '.rtf'

    @staticmethod
    def extract_text(file_path: str, parameters: Dict[str, Any]) -> str:
        amount = parameters.get('amount', 2000)

        # Читаем файл с определением кодировки
        raw = RTFHandler._read_rtf_bytes(file_path)
        if not raw:
            return ""

        # --- Попытка 1: striprtf ---
        text = RTFHandler._extract_with_striprtf(raw)
        if text:
            return text[:amount]

        # --- Попытка 2: regex fallback ---
        text = RTFHandler._extract_regex(raw)
        return text[:amount]

    @staticmethod
    def get_metadata(file_path: str) -> Dict[str, str]:
        """Извлекает title и author из info-блока RTF если есть."""
        raw = RTFHandler._read_rtf_bytes(file_path)
        if not raw:
            return {}
        try:
            text = raw.decode('utf-8', errors='ignore')
        except Exception:
            return {}

        metadata = {}
        for field, pattern in (
            ('title',  r'\\title\s+([^{}\\]+)'),
            ('author', r'\\author\s+([^{}\\]+)'),
        ):
            m = re.search(pattern, text)
            if m:
                metadata[field] = m.group(1).strip()
        return metadata

    # ------------------------------------------------------------------

    @staticmethod
    def _read_rtf_bytes(file_path: str) -> bytes:
        try:
            with open(file_path, 'rb') as f:
                return f.read()
        except Exception as e:
            logger.error(f"Ошибка чтения RTF {file_path}: {e}")
            return b""

    @staticmethod
    def _extract_with_striprtf(raw: bytes) -> str:
        try:
            from striprtf.striprtf import rtf_to_text

            # striprtf ожидает строку; пробуем кодировки
            for enc in ('utf-8', 'cp1251', 'cp866', 'latin-1'):
                try:
                    text = rtf_to_text(raw.decode(enc, errors='strict'))
                    text = text.strip()
                    if len(text) > 30:
                        return text
                except (UnicodeDecodeError, Exception):
                    continue
            return ""
        except ImportError:
            logger.debug("striprtf не установлен (pip install striprtf)")
            return ""
        except Exception as e:
            logger.debug(f"striprtf ошибка: {e}")
            return ""

    @staticmethod
    def _extract_regex(raw: bytes) -> str:
        """Простая очистка RTF-тегов регулярками — работает без зависимостей."""
        try:
            # Декодируем
            for enc in ('utf-8', 'cp1251', 'cp866', 'latin-1'):
                try:
                    text = raw.decode(enc, errors='strict')
                    break
                except UnicodeDecodeError:
                    continue
            else:
                text = raw.decode('latin-1', errors='replace')

            # Убираем RTF-заголовок и служебные блоки
            text = re.sub(r'\{\\fonttbl[^}]*\}', '', text)
            text = re.sub(r'\{\\colortbl[^}]*\}', '', text)
            text = re.sub(r'\{\\stylesheet[^}]*\}', '', text)
            text = re.sub(r'\{\\info[^}]*\}', '', text)
            text = re.sub(r'\{\\pict[^}]*\}', '', text)

            # Обрабатываем \uNNNN? (Unicode escape в RTF)
            def replace_unicode(m):
                try:
                    return chr(int(m.group(1)))
                except Exception:
                    return ''
            text = re.sub(r'\\u(-?\d+)\??', replace_unicode, text)

            # Убираем все RTF-команды и скобки
            text = re.sub(r'\\[a-zA-Z]+\-?\d*\s?', '', text)
            text = re.sub(r'[{}]', '', text)
            text = re.sub(r'\\\'[0-9a-fA-F]{2}', '', text)  # hex-escape

            # Нормализуем пробелы
            text = re.sub(r'\s+', ' ', text).strip()
            return text if len(text) > 10 else ""

        except Exception as e:
            logger.debug(f"RTF regex fallback ошибка: {e}")
            return ""
