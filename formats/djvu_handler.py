import os
import logging
import shutil
import subprocess
import tempfile
from typing import Dict, Any, Optional
from .base_handler import BaseFormatHandler

logger = logging.getLogger(__name__)

# Стандартные пути к DjVuLibre на Windows
DJVU_SEARCH_PATHS = [
    r"C:\Program Files (x86)\DjVuLibre",
    r"C:\Program Files (x86)\DjVuLibre\bin",
    r"C:\Program Files\DjVuLibre",
    r"C:\Program Files\DjVuLibre\bin",
    r"C:\gnuwin32\bin",
    r"C:\gnuwin32\djvulibre\bin",
    r"C:\DjVuLibre\bin",
    r"C:\tools\djvulibre\bin",
]


def _find_djvu_tool(name: str) -> Optional[str]:
    """
    Ищет исполняемый файл DjVuLibre (djvutxt, ddjvu) в PATH и типичных местах.
    Возвращает полный путь или None.
    """
    # Сначала ищем в PATH
    found = shutil.which(name) or shutil.which(name + ".exe")
    if found:
        logger.info(f"Найден {name} в PATH: {found}")
        return found

    # Потом — в известных каталогах
    for directory in DJVU_SEARCH_PATHS:
        candidate = os.path.join(directory, name + ".exe")
        if os.path.isfile(candidate):
            logger.info(f"Найден {name}: {candidate}")
            return candidate

    return None


class DJVUHandler(BaseFormatHandler):
    """
    Обработчик для DJVU файлов.

    Стратегия:
      1. djvutxt  — быстрое извлечение встроенного текстового слоя (если есть)
      2. ddjvu + pytesseract — OCR для отсканированных страниц
    """

    @staticmethod
    def can_handle(file_path: str) -> bool:
        return BaseFormatHandler.get_file_extension(file_path) == ".djvu"

    @staticmethod
    def extract_text(file_path: str, parameters: Dict[str, Any]) -> str:
        amount = parameters.get("amount", 2000)
        pages  = 3  # читаем первые 3 страницы

        # --- Попытка 1: djvutxt (текстовый слой, мгновенно) ---
        text = DJVUHandler._extract_with_djvutxt(file_path, pages, amount)
        if text:
            logger.info(f"djvutxt: извлечено {len(text)} символов")
            return text

        # --- Попытка 2: ddjvu + OCR ---
        logger.info("Текстового слоя нет, пробуем OCR через ddjvu...")
        text = DJVUHandler._extract_with_ocr(file_path, pages, amount)
        if text:
            logger.info(f"OCR: извлечено {len(text)} символов")
            return text

        logger.warning(
            f"Не удалось извлечь текст из {os.path.basename(file_path)}.\n"
            "Убедитесь что DjVuLibre установлен и доступен.\n"
            "Скачать: https://sourceforge.net/projects/djvu/files/DjVuLibre_Windows/"
        )
        return ""

    @staticmethod
    def _extract_with_djvutxt(file_path: str, pages: int, max_chars: int) -> str:
        """Извлекает встроенный текст через djvutxt."""
        tool = _find_djvu_tool("djvutxt")
        if not tool:
            logger.warning("djvutxt не найден. Искал в PATH и: %s", DJVU_SEARCH_PATHS)
            return ""

        try:
            result = subprocess.run(
                [tool, f"--page=1-{pages}", file_path],
                capture_output=True, timeout=30,
                # DjVuLibre на Windows может отдавать cp1251
            )
            # Пробуем декодировать в разных кодировках
            for enc in ("utf-8", "cp1251", "cp866"):
                try:
                    text = result.stdout.decode(enc).strip()
                    if text:
                        return text[:max_chars]
                except UnicodeDecodeError:
                    continue
            return ""
        except subprocess.TimeoutExpired:
            logger.warning("djvutxt: таймаут")
            return ""
        except Exception as e:
            logger.debug(f"djvutxt ошибка: {e}")
            return ""

    @staticmethod
    def _extract_with_ocr(file_path: str, pages: int, max_chars: int) -> str:
        """Конвертирует страницы через ddjvu и распознаёт текст через tesseract."""
        tool = _find_djvu_tool("ddjvu")
        if not tool:
            logger.warning("ddjvu не найден. Искал в PATH и: %s", DJVU_SEARCH_PATHS)
            return ""

        try:
            from PIL import Image
            from .ocr_utils import perform_ocr_images
        except ImportError as e:
            logger.warning(f"OCR недоступен: {e}")
            return ""

        images = []
        with tempfile.TemporaryDirectory() as tmpdir:
            for page_num in range(1, pages + 1):
                out_path = os.path.join(tmpdir, f"page_{page_num}.pnm")
                try:
                    result = subprocess.run(
                        [tool, "-format=pnm", f"-page={page_num}",
                         file_path, out_path],
                        capture_output=True, timeout=60
                        # check=True убран — анализируем returncode вручную
                    )
                    if result.returncode != 0:
                        err = ""
                        for enc in ("utf-8", "cp1251", "cp866"):
                            try:
                                err = (result.stderr or b"").decode(enc).strip()
                                break
                            except UnicodeDecodeError:
                                continue
                        logger.warning(f"ddjvu страница {page_num}, код {result.returncode}: {err}")
                        break
                    if os.path.isfile(out_path) and os.path.getsize(out_path) > 0:
                        img = Image.open(out_path)
                        img.load()   # принудительно читаем до закрытия tmpdir
                        images.append(img.copy())
                    else:
                        logger.warning(f"ddjvu: выходной файл пустой для страницы {page_num}")
                        break
                except subprocess.TimeoutExpired:
                    logger.warning(f"ddjvu: таймаут на странице {page_num}")
                    break
                except Exception as e:
                    logger.warning(f"ddjvu страница {page_num}: {e}")
                    break

        if not images:
            return ""

        return perform_ocr_images(images, lang="rus+eng", max_chars=max_chars)

    @staticmethod
    def get_metadata(file_path: str) -> Dict[str, str]:
        """Извлекает метаданные из DJVU через djvused."""
        tool = _find_djvu_tool("djvused")
        if not tool:
            return {}
        try:
            result = subprocess.run(
                [tool, file_path, "-e", "print-meta"],
                capture_output=True, timeout=10
            )
            text = ""
            for enc in ("utf-8", "cp1251"):
                try:
                    text = result.stdout.decode(enc)
                    break
                except UnicodeDecodeError:
                    continue

            metadata = {}
            for line in text.splitlines():
                if " " in line:
                    key, _, value = line.partition(" ")
                    metadata[key.strip()] = value.strip().strip('"')
            return metadata
        except Exception:
            return {}
