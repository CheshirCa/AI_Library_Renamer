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
      2. Чистый ASCII
      3. Чистый UTF-8 (strict)
      4. Эвристика по байтовым диапазонам cp866 vs cp1251:
           cp866: А-Я = 0x80-0x9F, а-п = 0xA0-0xAF, р-я = 0xE0-0xEF,
                  псевдографика = 0xB0-0xDF (уникальный маркер!)
           cp1251: А-Я а-я = 0xC0-0xFF
           Перекрытие: 0xE0-0xEF — и cp866(р-я) и cp1251(а-п).
           Решающий сигнал: 0xB0-0xDF в cp866 — псевдографика (DIZ/NFO).
                            0x80-0x9F в cp866 — заглавные; в cp1251 — спецсимволы.
      5. charset_normalizer (уточнение при неопределённости)
      6. Fallback: cp1251
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

    # Чистый ASCII
    try:
        data.decode('ascii')
        return 'ascii'
    except UnicodeDecodeError:
        pass

    # Чистый UTF-8
    try:
        data.decode('utf-8', errors='strict')
        return 'utf-8'
    except UnicodeDecodeError:
        pass

    # --- Эвристика cp866 vs cp1251 ---
    #
    # cp866 диапазоны:
    #   0x80-0x9F = А-Я (заглавные)          → в cp1251 это спецсимволы (€, №, ©...)
    #   0xA0-0xAF = а-п (строчные)
    #   0xB0-0xDF = псевдографика             → в cp1251 это спецсимволы/латиница
    #   0xE0-0xEF = р-я (строчные)            → в cp1251 это тоже р-я (пересечение!)
    #   0xF0      = Ё, 0xF1 = ё
    #
    # cp1251 диапазоны:
    #   0xC0-0xEF = А-п (заглавные и строчные а-п)
    #   0xF0-0xFF = р-я

    cp866_upper  = sum(1 for b in data if 0x80 <= b <= 0x9F)   # заглавные А-Я
    cp866_lower  = sum(1 for b in data if 0xA0 <= b <= 0xAF)   # строчные а-п
    cp866_pseudo = sum(1 for b in data if 0xB0 <= b <= 0xDF)   # псевдографика ─┼╔ (сильный маркер)
    cp866_ry     = sum(1 for b in data if 0xE0 <= b <= 0xEF)   # строчные р-я (пересечение)

    cp1251_upper = sum(1 for b in data if 0xC0 <= b <= 0xEF)   # А-п в cp1251
    cp1251_lower = sum(1 for b in data if 0xF0 <= b <= 0xFF)   # р-я в cp1251

    # Сильный маркер cp866: псевдографика и/или заглавные в диапазоне 0x80-0x9F
    # (в cp1251 эти байты — спецсимволы, редко встречаются в русском тексте)
    cp866_score  = cp866_upper * 3 + cp866_lower * 2 + cp866_pseudo * 4 + cp866_ry
    cp1251_score = cp1251_upper * 2 + cp1251_lower * 3

    if cp866_score > cp1251_score * 1.2:
        return 'cp866'

    if cp1251_score > cp866_score * 1.2:
        return 'cp1251'

    # Неопределённость — уточняем через charset_normalizer
    try:
        from charset_normalizer import from_bytes
        result = from_bytes(data, cp_isolation=['cp1251', 'cp866', 'utf-8'])
        best = result.best()
        if best:
            enc = str(best.encoding)
            logger.debug(f"charset_normalizer уточнил кодировку: {enc}")
            return enc
    except Exception:
        pass

    # Финальный fallback
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
