"""
chm_handler.py — обработчик для CHM (Compiled HTML Help) файлов.

CHM — это архив в формате Microsoft IStorage/ITSS содержащий HTML-страницы.

Стратегия:
  1. pychm (pip install pychm) — прямое чтение CHM
  2. chm2txt / hh.exe через subprocess — внешние инструменты
  3. Бинарный поиск HTML-фрагментов прямо в байтах файла (fallback без зависимостей)
"""

import os
import re
import shutil
import logging
import subprocess
import tempfile
from typing import Dict, Any, Optional
from .base_handler import BaseFormatHandler

logger = logging.getLogger(__name__)


class CHMHandler(BaseFormatHandler):
    """Обработчик для CHM файлов."""

    @staticmethod
    def can_handle(file_path: str) -> bool:
        return BaseFormatHandler.get_file_extension(file_path) == '.chm'

    @staticmethod
    def extract_text(file_path: str, parameters: Dict[str, Any]) -> str:
        amount = parameters.get('amount', 2000)

        # --- Попытка 1: pychm ---
        text = CHMHandler._extract_with_pychm(file_path, amount)
        if text:
            logger.info(f"CHM pychm: извлечено {len(text)} символов")
            return text

        # --- Попытка 2: 7-Zip (умеет распаковывать CHM) ---
        text = CHMHandler._extract_with_7zip(file_path, amount)
        if text:
            logger.info(f"CHM 7zip: извлечено {len(text)} символов")
            return text

        # --- Попытка 3: бинарный поиск HTML в байтах ---
        text = CHMHandler._extract_binary(file_path, amount)
        if text:
            logger.info(f"CHM binary: извлечено {len(text)} символов")
            return text

        logger.warning(f"Не удалось извлечь текст из CHM: {os.path.basename(file_path)}")
        return ""

    @staticmethod
    def get_metadata(file_path: str) -> Dict[str, str]:
        """Извлекает title из CHM (через pychm или бинарный поиск)."""
        try:
            import chm.chm as chmlib
            c = chmlib.CHMFile()
            if c.LoadCHM(file_path):
                title = c.GetEncoding() and ""  # GetTitle не всегда есть
                # Пробуем достать из HHCTRL.OCX metadata
                c.CloseCHM()
        except Exception:
            pass

        # Fallback: ищем <title> в байтах
        try:
            with open(file_path, 'rb') as f:
                data = f.read(min(64 * 1024, os.path.getsize(file_path)))
            # CHM хранит HTML внутри, ищем тег title
            m = re.search(rb'<title[^>]*>(.*?)</title>', data, re.IGNORECASE | re.DOTALL)
            if m:
                raw = m.group(1).decode('utf-8', errors='replace').strip()
                raw = re.sub(r'<[^>]+>', '', raw).strip()
                if raw:
                    return {'title': raw}
        except Exception:
            pass
        return {}

    # ------------------------------------------------------------------

    @staticmethod
    def _extract_with_pychm(file_path: str, max_chars: int) -> str:
        """Извлекает текст через библиотеку pychm."""
        try:
            import chm.chm as chmlib
            import chm.chmdump as chmdump
        except ImportError:
            logger.debug("pychm не установлен (pip install pychm)")
            return ""

        try:
            c = chmlib.CHMFile()
            if not c.LoadCHM(file_path):
                return ""

            text_parts = []
            total = 0

            def collect(chm_file, ui, context):
                nonlocal total
                if total >= max_chars:
                    return 1  # стоп
                path = ui.path.decode('utf-8', errors='replace') if isinstance(ui.path, bytes) else ui.path
                if not path.lower().endswith(('.htm', '.html')):
                    return 0
                try:
                    result, data = chm_file.RetrieveObject(ui)
                    if result and data:
                        html = data.decode('utf-8', errors='replace')
                        # Убираем теги
                        clean = re.sub(r'<[^>]+>', ' ', html)
                        clean = re.sub(r'\s+', ' ', clean).strip()
                        if len(clean) > 30:
                            text_parts.append(clean)
                            total += len(clean)
                except Exception:
                    pass
                return 0

            c.EnumerateFiles(collect, None)
            c.CloseCHM()

            return ' '.join(text_parts)[:max_chars]

        except Exception as e:
            logger.debug(f"pychm ошибка: {e}")
            return ""

    @staticmethod
    def _decode_html(raw: bytes) -> str:
        """
        Декодирует HTML-файл из CHM с автоопределением кодировки.

        Порядок:
          1. charset из <meta> тегов (большинство старых CHM объявляют windows-1251)
          2. Чистый UTF-8 (strict)
          3. cp1251 — основная кодировка русских CHM 1990-2000х годов
          4. cp866, latin-1
        """
        # 1. Ищем charset в meta charset
        m = re.search(
            rb'<meta[^>]+charset=["\']?\s*([a-zA-Z0-9_\-]+)',
            raw[:4096], re.IGNORECASE
        )
        if m:
            declared = m.group(1).decode('ascii', errors='replace').lower().strip()
            enc_map = {
                'windows-1251': 'cp1251', 'win-1251': 'cp1251',
                'utf-8': 'utf-8', 'utf8': 'utf-8',
                'koi8-r': 'koi8_r', 'koi8r': 'koi8_r',
                'cp866': 'cp866', 'ibm866': 'cp866',
            }
            enc = enc_map.get(declared, declared)
            try:
                return raw.decode(enc, errors='replace')
            except (LookupError, UnicodeDecodeError):
                pass

        # 2. Чистый UTF-8
        try:
            return raw.decode('utf-8', errors='strict')
        except UnicodeDecodeError:
            pass

        # 3. Эвристика cp1251 vs cp866 (из txt_handler логики)
        cp1251_upper = sum(1 for b in raw if 0xC0 <= b <= 0xEF)
        cp866_pseudo = sum(1 for b in raw if 0xB0 <= b <= 0xDF)
        cp866_upper  = sum(1 for b in raw if 0x80 <= b <= 0x9F)

        cp866_score  = cp866_upper * 3 + cp866_pseudo * 4
        cp1251_score = cp1251_upper * 2

        if cp866_score > cp1251_score * 1.2:
            return raw.decode('cp866', errors='replace')

        # cp1251 по умолчанию для русских Windows-приложений
        return raw.decode('cp1251', errors='replace')

    @staticmethod
    def _extract_with_7zip(file_path: str, max_chars: int) -> str:
        """Распаковывает CHM через 7-Zip и читает HTML-файлы."""
        z = shutil.which('7z') or shutil.which('7z.exe')
        if not z:
            for p in [r"C:\Program Files\7-Zip\7z.exe",
                      r"C:\Program Files (x86)\7-Zip\7z.exe"]:
                if os.path.isfile(p):
                    z = p
                    break
        if not z:
            logger.debug("7-Zip не найден")
            return ""

        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                result = subprocess.run(
                    [z, 'e', '-y', f'-o{tmpdir}', file_path, '*.htm', '*.html', '-r'],
                    capture_output=True, timeout=30
                )
                text_parts = []
                total = 0
                for root, _, files in os.walk(tmpdir):
                    for fname in sorted(files):
                        if not fname.lower().endswith(('.htm', '.html')):
                            continue
                        try:
                            with open(os.path.join(root, fname), 'rb') as f:
                                raw = f.read()
                            html = CHMHandler._decode_html(raw)
                            clean = re.sub(r'<[^>]+>', ' ', html)
                            clean = re.sub(r'\s+', ' ', clean).strip()
                            if len(clean) > 30:
                                text_parts.append(clean)
                                total += len(clean)
                            if total >= max_chars:
                                break
                        except Exception:
                            continue
                    if total >= max_chars:
                        break
                return ' '.join(text_parts)[:max_chars]
        except Exception as e:
            logger.debug(f"7-Zip CHM ошибка: {e}")
            return ""

    @staticmethod
    def _extract_binary(file_path: str, max_chars: int) -> str:
        """
        Ищет читаемые HTML-фрагменты прямо в байтах CHM файла.
        Работает без зависимостей — CHM хранит сжатые HTML внутри,
        иногда часть текста доступна без распаковки.
        """
        try:
            with open(file_path, 'rb') as f:
                data = f.read(min(256 * 1024, os.path.getsize(file_path)))

            # Ищем HTML-блоки
            parts = []
            for m in re.finditer(rb'<(?:h[1-4]|title|p)[^>]*>(.*?)</(?:h[1-4]|title|p)>',
                                  data, re.IGNORECASE | re.DOTALL):
                raw = m.group(1)
                # Декодируем
                for enc in ('utf-8', 'cp1251', 'cp866', 'latin-1'):
                    try:
                        text = raw.decode(enc)
                        break
                    except UnicodeDecodeError:
                        continue
                else:
                    continue
                clean = re.sub(r'<[^>]+>', '', text).strip()
                clean = re.sub(r'\s+', ' ', clean)
                if len(clean) > 5:
                    parts.append(clean)
                if sum(len(p) for p in parts) >= max_chars:
                    break

            return ' '.join(parts)[:max_chars]

        except Exception as e:
            logger.debug(f"CHM binary ошибка: {e}")
            return ""
