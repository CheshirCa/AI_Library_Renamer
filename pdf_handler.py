import os
import logging
from typing import Dict, Any
from .base_handler import BaseFormatHandler

logger = logging.getLogger(__name__)

_VERSION = "3.3.0"  # удалён дубль extract_text; OCR через pymupdf рендеринг (JPXDecode fix)


class PDFHandler(BaseFormatHandler):
    """Обработчик для PDF файлов (pymupdf → PyPDF2 → OCR)"""

    # Минимальная доля читаемых слов.
    # Если меньше — текст мусор из кастомных шрифтов без ToUnicode
    _MIN_READABLE_RATIO = 0.55

    @staticmethod
    def can_handle(file_path: str) -> bool:
        return BaseFormatHandler.get_file_extension(file_path) == ".pdf"

    @staticmethod
    def _text_quality(text: str) -> float:
        """
        Оценивает качество извлечённого текста по структуре слов.
        ВАЖНО: вызывать только на тексте первой страницы (первые 500 символов),
        не на всём документе — дальние страницы могут иметь нормальный шрифт
        и замаскировать проблему с титульной страницей.
        """
        import re
        if not text or len(text.strip()) < 10:
            return 0.0
        words = re.findall(r'[a-zA-Zа-яёА-ЯЁ]+', text)
        if not words:
            return 0.0
        if len(words) < 3:
            return 0.8
        single_char  = sum(1 for w in words if len(w) == 1)
        long_words   = sum(1 for w in words if len(w) >= 4)
        avg_len      = sum(len(w) for w in words) / len(words)
        single_ratio = single_char / len(words)
        long_ratio   = long_words / len(words)
        if single_ratio > 0.35 and avg_len < 3.0:
            return 0.2
        if single_ratio >= 0.45:
            return 0.2
        if len(words) <= 6 and single_ratio >= 0.35:
            return 0.2
        if avg_len < 2.0:
            return 0.1
        if long_ratio < 0.15 and len(words) > 10:
            return 0.3
        return 0.9

    # Sentinel: первая страница содержит мусор — PyPDF2 не поможет, нужен OCR
    _GARBLED_FIRST_PAGE = "__GARBLED__"

    @staticmethod
    def extract_text(file_path: str, parameters: Dict[str, Any]) -> str:
        action_type = parameters.get("type", "first_chars")
        amount      = parameters.get("amount", 500)

        logger.debug(f"PDFHandler v{_VERSION}")

        # --- Попытка 1: pdftotext (poppler) — лучшая поддержка ToUnicode ---
        text = PDFHandler._extract_with_pdftotext(file_path, action_type, amount)
        if text:
            logger.info(f"pdftotext: извлечено {len(text)} символов")
            return text

        # --- Попытка 2: pymupdf ---
        try:
            import fitz
            result = PDFHandler._extract_with_fitz(fitz, file_path, action_type, amount)
            if result == PDFHandler._GARBLED_FIRST_PAGE:
                logger.info("pymupdf: первая страница мусор → OCR")
                return PDFHandler._ocr_pdf(file_path, amount, action_type)
            if result:
                return result
        except ImportError:
            logger.warning("pymupdf не установлен, пробуем PyPDF2...")
        except Exception as e:
            logger.warning(f"pymupdf ошибка: {e}, пробуем PyPDF2...")

        # --- Попытка 3: PyPDF2 ---
        try:
            import PyPDF2
            text = PDFHandler._extract_with_pypdf2(PyPDF2, file_path, action_type, amount)
            if text:
                return text
        except ImportError:
            logger.warning("PyPDF2 не установлен")
        except Exception as e:
            logger.warning(f"PyPDF2 ошибка: {e}")

        # --- Попытка 4: OCR ---
        return PDFHandler._ocr_pdf(file_path, amount, action_type)

    @staticmethod
    def _extract_with_pdftotext(file_path: str, action_type: str, amount: int) -> str:
        """
        Извлекает текст через pdftotext из пакета poppler.
        Использует временный файл вместо stdout (gnuwin32 не всегда поддерживает '-').
        Poppler лучше pymupdf справляется с нестандартными ToUnicode маппингами.
        """
        import subprocess, shutil, tempfile

        tool = shutil.which("pdftotext") or shutil.which("pdftotext.exe")
        if not tool:
            poppler = PDFHandler._find_poppler()
            if poppler:
                for name in ("pdftotext.exe", "pdftotext"):
                    candidate = os.path.join(poppler, name)
                    if os.path.isfile(candidate):
                        tool = candidate
                        break
        if not tool:
            logger.debug("pdftotext не найден")
            return ""

        logger.debug(f"pdftotext найден: {tool}")

        # Используем tempfile в %TEMP% — заведомо ASCII-путь без проблем с кодировкой
        try:
            with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as tmp:
                tmp_path = tmp.name

            result = subprocess.run(
                [tool, "-enc", "UTF-8", "-layout", file_path, tmp_path],
                capture_output=True, timeout=30
            )

            logger.debug(f"pdftotext returncode={result.returncode}")

            # Читаем результат из файла независимо от returncode:
            # JPXDecode/JBIG2 ошибки (картинки) дают код 2+, но текстовый слой записан нормально.
            try:
                file_size = os.path.getsize(tmp_path)
            except OSError:
                file_size = 0

            text = ""
            if file_size > 0:
                try:
                    with open(tmp_path, "r", encoding="utf-8", errors="replace") as f:
                        text = f.read().strip()
                except Exception as e:
                    logger.debug(f"pdftotext: не удалось прочитать temp-файл: {e}")
            elif result.returncode not in (0, 1):
                err = result.stderr.decode("utf-8", errors="replace")[:200]
                logger.debug(f"pdftotext stderr: {err}")

            try:
                os.unlink(tmp_path)
            except Exception:
                pass

            if not text:
                logger.debug("pdftotext: пустой результат")
                return ""

            logger.info(f"pdftotext: извлечено {len(text)} символов")
            return text[:amount] if action_type == "first_chars" else text

        except subprocess.TimeoutExpired:
            logger.warning("pdftotext: таймаут")
            return ""
        except Exception as e:
            logger.debug(f"pdftotext ошибка: {e}")
            return ""


    @staticmethod
    def _extract_with_fitz(fitz, file_path: str, action_type: str, amount: int) -> str:
        doc        = fitz.open(file_path)
        parts      = []
        total      = 0
        page1_text = ""  # текст первой страницы (может быть пустым если графика)

        for page_num, page in enumerate(doc):
            t = page.get_text()

            if page_num == 0:
                page1_text = t.strip()
                if not page1_text:
                    # Страница 1 пустая — текст как вектор/изображение.
                    # Заголовок там, но невидим для pymupdf.
                    # Помечаем — после сборки добавим OCR первой страницы.
                    logger.info("pymupdf: страница 1 пустая (заголовок как графика), нужен OCR стр.1")
                    continue
                q = PDFHandler._text_quality(page1_text)
                logger.info(
                    f"pymupdf: стр.1 quality={q:.2f} "
                    f"(chars={len(page1_text)}, threshold={PDFHandler._MIN_READABLE_RATIO})"
                )
                if q < PDFHandler._MIN_READABLE_RATIO:
                    doc.close()
                    logger.info("pymupdf: стр.1 мусор (кастомный шрифт) → OCR")
                    return PDFHandler._GARBLED_FIRST_PAGE

            if not t.strip():
                continue

            parts.append(t)
            total += len(t)
            if action_type == "first_chars" and total >= amount:
                break

        doc.close()
        combined = "\n".join(parts)

        if not combined.strip():
            return ""  # нет текстового слоя вообще → PyPDF2/OCR

        # Если страница 1 была пустой — добавляем OCR первой страницы в начало
        # чтобы LLM увидел заголовок, а не только содержимое слайдов
        if not page1_text:
            ocr_title = PDFHandler._ocr_first_page(file_path)
            if ocr_title:
                combined = ocr_title + "\n\n" + combined

        return combined[:amount] if action_type == "first_chars" else combined

    @staticmethod
    def _extract_with_pypdf2(PyPDF2, file_path: str, action_type: str, amount: int) -> str:
        text = ""
        with open(file_path, "rb") as f:
            reader = PyPDF2.PdfReader(f)
            for page in reader.pages:
                t = page.extract_text() or ""
                text += t + "\n"
                if action_type == "first_chars" and len(text) >= amount:
                    break
        result = text.strip()
        return text[:amount] if (result and action_type == "first_chars") else (text if result else "")

    @staticmethod
    def _render_pages_fitz(file_path: str, first_page: int, last_page: int,
                           dpi: int = 150) -> list:
        """
        Рендерит страницы PDF через pymupdf → список PIL.Image.
        Работает с JPXDecode и JBIG2 — MuPDF поддерживает эти фильтры нативно,
        в отличие от старых сборок poppler/pdftoppm.
        """
        try:
            import fitz
            from PIL import Image
            doc = fitz.open(file_path)
            mat = fitz.Matrix(dpi / 72, dpi / 72)
            images = []
            for page_num in range(first_page - 1, min(last_page, len(doc))):
                pix = doc[page_num].get_pixmap(matrix=mat)
                img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
                images.append(img)
            doc.close()
            return images
        except Exception as e:
            logger.debug(f"fitz rendering: {e}")
            return []

    @staticmethod
    def _ocr_first_page(file_path: str) -> str:
        """OCR первой страницы — для заголовков нарисованных как графика."""
        try:
            from .ocr_utils import perform_ocr_images
        except ImportError as e:
            return ""

        # Попытка 1: pymupdf (поддерживает JPXDecode/JBIG2)
        images = PDFHandler._render_pages_fitz(file_path, 1, 1, dpi=200)

        if not images:
            # Попытка 2: pdftoppm через pdf2image
            try:
                from pdf2image import convert_from_path
                poppler_path = PDFHandler._find_poppler()
                kwargs = {"first_page": 1, "last_page": 1, "dpi": 200}
                if poppler_path:
                    kwargs["poppler_path"] = poppler_path
                images = convert_from_path(file_path, **kwargs)
            except Exception as e:
                logger.debug(f"OCR первой страницы не удался: {e}")
                return ""

        if not images:
            return ""
        text = perform_ocr_images(images, lang="rus+eng", max_chars=1000)
        if text:
            logger.info(f"OCR стр.1: извлечено {len(text)} символов")
        return text

    @staticmethod
    def _ocr_pdf(file_path: str, amount: int, action_type: str) -> str:
        """OCR через pymupdf-рендеринг (основной) или pdf2image (fallback)."""
        try:
            from .ocr_utils import perform_ocr_images
        except ImportError as e:
            return f"OCR недоступен: {e}. Установите pytesseract и pillow."

        # Попытка 1: pymupdf рендеринг (поддерживает JPXDecode/JBIG2)
        images = PDFHandler._render_pages_fitz(file_path, 1, 3, dpi=150)

        if not images:
            # Попытка 2: pdftoppm через pdf2image (может не поддерживать JPX)
            try:
                from pdf2image import convert_from_path
                poppler_path = PDFHandler._find_poppler()
                kwargs = {"first_page": 1, "last_page": 3, "dpi": 150}
                if poppler_path:
                    kwargs["poppler_path"] = poppler_path
                images = convert_from_path(file_path, **kwargs)
            except Exception as e:
                logger.error(f"OCR PDF не удался: {e}")
                return ""

        if not images:
            return ""

        max_chars = amount if action_type == "first_chars" else None
        return perform_ocr_images(images, lang="rus+eng", max_chars=max_chars)

    @staticmethod
    def _find_poppler() -> str:
        """
        Ищет poppler на Windows в типичных местах установки.
        Возвращает путь к папке bin или пустую строку (тогда pdf2image ищет сам).
        """
        import shutil
        if shutil.which("pdftoppm"):
            return ""  # уже в PATH

        candidates = [
            r"C:\gnuwin32\poppler\bin",
            r"C:\poppler\Library\bin",
            r"C:\poppler\bin",
            r"C:\Program Files\poppler\bin",
            r"C:\Program Files\poppler\Library\bin",
            r"C:\tools\poppler\Library\bin",
        ]
        for path in candidates:
            if os.path.isfile(os.path.join(path, "pdftoppm.exe")):
                logger.info(f"Найден poppler: {path}")
                return path

        logger.warning(
            "poppler не найден. OCR для PDF недоступен.\n"
            "Скачайте poppler: https://github.com/oschwartz10612/poppler-windows/releases\n"
            "Распакуйте и добавьте папку bin в PATH, или положите в C:\\poppler\\Library\\bin"
        )
        return ""

    @staticmethod
    def get_metadata(file_path: str) -> Dict[str, str]:
        try:
            import fitz
            doc = fitz.open(file_path)
            raw = doc.metadata
            doc.close()
            keys_map = {
                "title": "title", "author": "author", "subject": "subject",
                "creator": "creator", "producer": "producer",
                "creationDate": "creation_date", "modDate": "modification_date",
            }
            return {v: raw[k] for k, v in keys_map.items() if raw.get(k)}
        except Exception:
            try:
                import PyPDF2
                with open(file_path, "rb") as f:
                    m = PyPDF2.PdfReader(f).metadata or {}
                return {k.lstrip("/"): str(v) for k, v in m.items() if v}
            except Exception:
                return {}
