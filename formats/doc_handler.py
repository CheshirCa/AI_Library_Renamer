import os
import re
import struct
import shutil
import logging
import subprocess
from typing import Dict, Any, Optional
from .base_handler import BaseFormatHandler

logger = logging.getLogger(__name__)

# Пути к antiword на Windows (если установлен)
ANTIWORD_SEARCH_PATHS = [
    r"C:\Program Files\Antiword",
    r"C:\Program Files (x86)\Antiword",
    r"C:\antiword",
    r"C:\tools\antiword",
    r"C:\gnuwin32\bin",
]


class DOCHandler(BaseFormatHandler):
    """
    Обработчик для DOC файлов (Word 97-2003, формат OLE/CFB).

    Стратегия:
      1. olefile  — читает OLE-структуру, извлекает WordDocument stream,
                    парсит текст из таблицы символов (надёжно, без внешних утилит)
      2. antiword — внешняя утилита, отличный результат для сложных DOC
      3. Бинарный grep — ищет читаемые строки прямо в байтах файла (fallback)
    """

    @staticmethod
    def can_handle(file_path: str) -> bool:
        ext = BaseFormatHandler.get_file_extension(file_path)
        if ext != '.doc':
            return False
        # Убеждаемся что это OLE, а не переименованный DOCX (ZIP)
        try:
            with open(file_path, 'rb') as f:
                magic = f.read(4)
            # OLE: D0 CF 11 E0 | DOCX/ZIP: PK\x03\x04
            return magic == b'\xd0\xcf\x11\xe0'
        except Exception:
            return True  # если не можем прочитать — пусть попробует

    @staticmethod
    def extract_text(file_path: str, parameters: Dict[str, Any]) -> str:
        amount = parameters.get('amount', 2000)

        # --- Попытка 1: olefile ---
        text = DOCHandler._extract_with_olefile(file_path)
        if text:
            logger.info(f"DOC olefile: извлечено {len(text)} символов")
            return text[:amount]

        # --- Попытка 2: antiword ---
        text = DOCHandler._extract_with_antiword(file_path)
        if text:
            logger.info(f"DOC antiword: извлечено {len(text)} символов")
            return text[:amount]

        # --- Попытка 3: бинарный grep ---
        text = DOCHandler._extract_binary_strings(file_path)
        if text:
            logger.info(f"DOC binary grep: извлечено {len(text)} символов")
            return text[:amount]

        logger.warning(f"Не удалось извлечь текст из DOC: {os.path.basename(file_path)}")
        return ""

    @staticmethod
    def get_metadata(file_path: str) -> Dict[str, str]:
        """Извлекает метаданные из OLE SummaryInformation."""
        try:
            import olefile
            if not olefile.isOleFile(file_path):
                return {}
            with olefile.OleFileIO(file_path) as ole:
                if not ole.exists('\x05SummaryInformation'):
                    return {}
                props = ole.getproperties('\x05SummaryInformation',
                                          convert_time=True)
                # PIDSI коды: 2=title, 4=author, 3=subject, 6=comments
                result = {}
                for code, key in ((2, 'title'), (4, 'author'),
                                   (3, 'subject'), (6, 'comments')):
                    val = props.get(code)
                    if val and isinstance(val, str):
                        result[key] = val.strip()
                return result
        except ImportError:
            return {}
        except Exception as e:
            logger.debug(f"DOC metadata error: {e}")
            return {}

    # ------------------------------------------------------------------

    @staticmethod
    def _extract_with_olefile(file_path: str) -> str:
        """
        Читает WordDocument stream через olefile.
        Word 97-2003 хранит текст в потоке WordDocument начиная с FIB (File Information Block).
        Извлекаем UTF-16LE строки из потока.
        """
        try:
            import olefile
        except ImportError:
            logger.debug("olefile не установлен (pip install olefile)")
            return ""

        try:
            if not olefile.isOleFile(file_path):
                return ""

            with olefile.OleFileIO(file_path) as ole:
                # Пробуем WordDocument stream
                if ole.exists('WordDocument'):
                    data = ole.openstream('WordDocument').read()
                elif ole.exists('worddocument'):
                    data = ole.openstream('worddocument').read()
                else:
                    return ""

                # Ищем UTF-16LE строки (Word 97+ хранит текст так)
                text_parts = []
                total = 0
                i = 0
                while i < len(data) - 1 and total < 4000:
                    # Пробуем декодировать 2-байтные последовательности
                    chunk = data[i:i+512]
                    try:
                        decoded = chunk.decode('utf-16-le', errors='ignore')
                        # Оставляем только читаемые символы
                        readable = ''.join(
                            c for c in decoded
                            if c.isprintable() or c in '\n\r\t '
                        )
                        readable = re.sub(r'\s+', ' ', readable).strip()
                        if len(readable) > 20:
                            text_parts.append(readable)
                            total += len(readable)
                    except Exception:
                        pass
                    i += 256  # шагаем с перекрытием

                text = ' '.join(text_parts)
                # Фильтруем мусор: оставляем только если кириллица/латиница > 30%
                cyrillic_latin = sum(
                    1 for c in text
                    if '\u0400' <= c <= '\u04FF' or c.isascii() and c.isalpha()
                )
                if len(text) > 0 and cyrillic_latin / len(text) > 0.3:
                    return text[:4000]
                return ""

        except Exception as e:
            logger.debug(f"olefile ошибка: {e}")
            return ""

    @staticmethod
    def _find_antiword() -> Optional[str]:
        found = shutil.which('antiword') or shutil.which('antiword.exe')
        if found:
            logger.info(f"antiword найден в PATH: {found}")
            return found
        for directory in ANTIWORD_SEARCH_PATHS:
            candidate = os.path.join(directory, 'antiword.exe')
            if os.path.isfile(candidate):
                logger.info(f"antiword найден: {candidate}")
                return candidate
        return None

    @staticmethod
    def _extract_with_antiword(file_path: str) -> str:
        tool = DOCHandler._find_antiword()
        if not tool:
            return ""
        try:
            result = subprocess.run(
                [tool, '-m', 'UTF-8', file_path],
                capture_output=True, timeout=30
            )
            if result.returncode == 0 and result.stdout:
                text = result.stdout.decode('utf-8', errors='ignore').strip()
                return text
            err = result.stderr.decode('utf-8', errors='ignore')[:200]
            logger.debug(f"antiword returncode {result.returncode}: {err}")
            return ""
        except subprocess.TimeoutExpired:
            logger.warning("antiword: таймаут")
            return ""
        except Exception as e:
            logger.debug(f"antiword ошибка: {e}")
            return ""

    @staticmethod
    def _extract_binary_strings(file_path: str) -> str:
        """
        Ищет читаемые строки напрямую в байтах файла.
        Работает всегда, но качество ниже — много мусора из служебных структур.
        """
        try:
            with open(file_path, 'rb') as f:
                data = f.read(64 * 1024)  # первые 64KB

            results = []

            # UTF-16LE строки (основной текст Word)
            utf16_strings = re.findall(
                b'(?:[\x20-\x7e\xc0-\xff]\x00){6,}',
                data
            )
            for s in utf16_strings:
                try:
                    decoded = s.decode('utf-16-le', errors='ignore').strip()
                    if len(decoded) > 5:
                        results.append(decoded)
                except Exception:
                    continue

            # ASCII строки (метаданные, стили)
            ascii_strings = re.findall(b'[\x20-\x7e\xc0-\xff]{8,}', data)
            for s in ascii_strings:
                try:
                    decoded = s.decode('cp1251', errors='ignore').strip()
                    if len(decoded) > 8 and any(c.isalpha() for c in decoded):
                        results.append(decoded)
                except Exception:
                    continue

            text = ' '.join(results)
            text = re.sub(r'\s+', ' ', text).strip()
            return text[:4000] if len(text) > 30 else ""

        except Exception as e:
            logger.debug(f"binary grep ошибка: {e}")
            return ""
