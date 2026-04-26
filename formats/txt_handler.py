import os
import logging
from typing import Dict, Any, Optional
from .base_handler import BaseFormatHandler

logger = logging.getLogger(__name__)


def detect_encoding(data: bytes) -> str:
    """
    Определяет кодировку байтовой строки.

    Порядок проверок:
      1. BOM-маркеры (UTF-8 BOM, UTF-16)
      2. charset_normalizer (входит в состав requests, точная детекция)
      3. Эвристика по характерным байтам cp1251 vs cp866
      4. Fallback: cp1251 (наиболее распространён для старых русских файлов)
    """
    if not data:
        return 'utf-8'

    # BOM
    if data.startswith(b'\xef\xbb\xbf'):
        return 'utf-8-sig'
    if data.startswith(b'\xff\xfe'):
        return 'utf-16-le'
    if data.startswith(b'\xfe\xff'):
        return 'utf-16-be'

    # Чистый ASCII — кодировка не важна
    try:
        data.decode('ascii')
        return 'ascii'
    except UnicodeDecodeError:
        pass

    # Чистый UTF-8 без BOM
    try:
        data.decode('utf-8', errors='strict')
        return 'utf-8'
    except UnicodeDecodeError:
        pass

    # charset_normalizer
    try:
        from charset_normalizer import from_bytes
        result = from_bytes(data, cp_isolation=['cp1251', 'cp866', 'utf-8', 'latin-1'])
        best = result.best()
        if best:
            enc = str(best.encoding)
            logger.debug(f"charset_normalizer: {enc} (confidence {best.chaos:.2f})")
            return enc
    except Exception:
        pass

    # Эвристика: cp866 использует 0x80-0xAF для букв А-я,
    # cp1251 использует 0xC0-0xFF. Считаем что встречается чаще.
    cp866_range  = sum(1 for b in data if 0x80 <= b <= 0xAF)
    cp1251_range = sum(1 for b in data if 0xC0 <= b <= 0xFF)
    if cp866_range > cp1251_range * 1.5:
        return 'cp866'

    return 'cp1251'


def decode_text(data: bytes) -> str:
    """Декодирует байты в строку с автоопределением кодировки."""
    enc = detect_encoding(data)
    try:
        return data.decode(enc, errors='replace')
    except Exception:
        return data.decode('cp1251', errors='replace')


class TXTHandler(BaseFormatHandler):
    """
    Обработчик для TXT файлов с автоопределением кодировки.
    Поддерживает UTF-8, UTF-8 BOM, cp1251, cp866, ASCII.
    """

    @staticmethod
    def can_handle(file_path: str) -> bool:
        return BaseFormatHandler.get_file_extension(file_path) == '.txt'

    @staticmethod
    def extract_text(file_path: str, parameters: Dict[str, Any]) -> str:
        amount = parameters.get('amount', 2000)
        try:
            with open(file_path, 'rb') as f:
                raw = f.read(amount * 4)  # читаем с запасом — многобайтные кодировки
            text = decode_text(raw)
            return text[:amount]
        except Exception as e:
            logger.error(f"Ошибка чтения TXT {file_path}: {e}")
            return ""
